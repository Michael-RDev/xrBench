"""Synthetic protocol tests; nothing here establishes physical device support."""

import copy
import hashlib
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from engine.native_reports import validate
from engine.public_server import PostgresReports, create_server
from engine.reports import digest


def test_reference_imports_do_not_modify_immutable_package(tmp_path):
    from engine.native_suite import references
    import sys

    source = tmp_path / "source/kernels"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    (source / "catalog.py").write_text("identity = 221\n")
    previous = sys.dont_write_bytecode
    with references(tmp_path) as catalog:
        assert catalog.identity == 221
        assert sys.dont_write_bytecode
    assert sys.dont_write_bytecode == previous
    assert not list(tmp_path.rglob("*.pyc"))


def native_fixture():
    sha = "a" * 64
    ids = [f"ai.onnx.ProtocolFixture{i}" for i in range(221)]
    config = dict(
        run_id="run-" + "1" * 32,
        backend="xrgpu_vulkan",
        mode="isolated",
        preset="standard",
        cases=ids,
        warmups=20,
        samples=100,
        execution_target="headset",
        suite_revision=sha,
        runtime_revision="b" * 40,
        native_package_sha256=sha,
        native_catalog_sha256=sha,
        apk_sha256=sha,
    )
    tensor = dict(
        name="Y",
        shape=[],
        dtype="int64",
        storage_dtype="int64",
        sha256=sha,
        file="output.bin",
        comparison=dict(atol=0, rtol=0, equal_nan=False),
    )
    cases = [
        dict(
            id=id,
            inputs=[],
            outputs=[copy.deepcopy(tensor)],
            attrs={},
            args={},
            contract_revision="b" * 40,
            contract_sha256=sha,
            shaders=["test.spv"],
        )
        for id in ids
    ]
    catalog = dict(cases=cases, runtime_files={"shaders/test.spv": sha})
    report = dict(
        schema_version=3,
        run_id=config["run_id"],
        configuration=config,
        status="complete",
        backend="xrgpu_vulkan",
        execution_location="headset",
        execution_context="foreground_service",
        device=dict(model="synthetic protocol fixture"),
        started_at="2026-10-26T00:00:00Z",
        finished_at="2026-10-26T00:00:01Z",
        **{
            k: config[k]
            for k in [
                "suite_revision",
                "runtime_revision",
                "native_package_sha256",
                "native_catalog_sha256",
            ]
        },
    )
    report["results"] = [
        dict(
            case=c["id"],
            operation=c["id"],
            phase="isolated",
            trial=0,
            status="unsupported",
            error="Synthetic capability skip",
            samples=[],
            shaders=[],
            **{
                k: copy.deepcopy(c[k])
                for k in [
                    "inputs",
                    "outputs",
                    "attrs",
                    "args",
                    "contract_revision",
                    "contract_sha256",
                ]
            },
        )
        for c in cases
    ]
    row = report["results"][0]
    row.update(
        status="passed",
        runtime_info=dict(backend="vulkan", cpu_compute_fallback=False),
        actual_outputs=[
            {k: tensor[k] for k in ["name", "shape", "dtype", "storage_dtype", "sha256"]}
        ],
        correctness=dict(passed=True),
        correctness_before=dict(passed=True),
        correctness_after=dict(passed=True),
        samples=[
            dict(
                execution_ms=1,
                gpu_ms=None,
                thermal_status=0,
                charging=False,
                display_interactive=False,
            )
            for _ in range(100)
        ],
        shaders=[dict(file="shaders/test.spv", sha256=sha)],
    )
    manifest = dict(
        apk_sha256=sha,
        configurations=dict(gpu={k: v for k, v in config.items() if k != "run_id"}),
        catalog=catalog,
    )
    return report, manifest


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def put(self, report):
        old = self.rows.get(report["run_id"])
        if old is not None and digest(old) != digest(report):
            raise FileExistsError("Different evidence")
        self.rows[report["run_id"]] = copy.deepcopy(report)
        return dict(sha256=digest(report))


def test_native_receiver_preserves_typed_bytes_and_confirms_committed_storage():
    report, manifest = native_fixture()
    payload = json.dumps(report).encode()
    store = MemoryStore()
    receiver = PostgresReports(store, [manifest])
    receipt = receiver.put(payload)
    assert receipt == receiver.put(payload)
    assert receipt["storage"] == "postgresql"
    assert receipt["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert store.rows[report["run_id"]]["native_report"] == report
    changed = copy.deepcopy(report)
    changed["device"]["model"] = "different"
    with pytest.raises(FileExistsError):
        receiver.put(json.dumps(changed).encode())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["results"].pop(),
        lambda r: r.update(test_only=True),
        lambda r: r["results"][0]["runtime_info"].update(test_only=True),
        lambda r: r["results"][0]["runtime_info"].update(cpu_compute_fallback=True),
        lambda r: r["results"][0]["outputs"][0].update(dtype="float32"),
        lambda r: r["results"][0]["actual_outputs"][0].update(sha256="0" * 64),
        lambda r: r["results"][0]["shaders"][0].update(sha256="0" * 64),
        lambda r: r["results"][0]["samples"][0].update(display_interactive=None),
    ],
)
def test_native_receiver_rejects_forged_or_incomplete_evidence(mutation):
    report, manifest = native_fixture()
    mutation(report)
    with pytest.raises(ValueError):
        validate(
            report,
            manifest["configurations"]["gpu"] | {"run_id": report["run_id"]},
            manifest["catalog"],
        )


def test_public_http_has_no_device_commands_and_retries_when_database_is_unavailable(tmp_path):
    report, manifest = native_fixture()
    (tmp_path / "index.html").write_text("Native browser runner")

    class Unavailable:
        def put(self, _):
            raise ConnectionError("PostgreSQL unavailable")

    server = create_server(Unavailable(), tmp_path, port=0, manifests=[manifest])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://localhost:{server.server_port}"
    try:
        assert urlopen(root).read() == b"Native browser runner"
        assert (
            urlopen(Request(root, headers={"Sec-Fetch-Site": "cross-site"})).read()
            == b"Native browser runner"
        )
        for path, body, headers, status in [
            ("/api/reports", json.dumps(report).encode(), {}, 503),
            ("/api/reports", json.dumps(report).encode(), {"Origin": "https://example.com"}, 403),
            ("/api/benchmark-sessions", b"{}", {}, 404),
            ("/api/reports", b'{"a":1,"a":2}', {}, 400),
        ]:
            with pytest.raises(HTTPError) as error:
                urlopen(
                    Request(
                        root + path,
                        data=body,
                        headers={"Content-Type": "application/json", **headers},
                    )
                )
            assert error.value.code == status
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
