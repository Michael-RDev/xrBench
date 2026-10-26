import numpy as np
import pytest

from engine.workloads import OPERATIONS, catalog, correctness, fixtures, module, reference, select


def test_catalog_and_presets():
    assert len(OPERATIONS)==26
    assert len({c["operation"] for c in catalog()["cases"]})==25
    assert len(catalog()["cases"])==63
    assert len(select(preset="smoke"))==63
    assert len(select(preset="standard"))==63
    assert select(preset="sustained")==catalog()["cases"]
    with pytest.raises(ValueError):select(preset="unknown")


def test_layer_source_edits_invalidate_bundle_revision(tmp_path, monkeypatch):
    from engine import workloads as layers

    script = tmp_path / "linear" / "layer.py"
    script.parent.mkdir()
    script.write_text("# original layer\n")
    monkeypatch.setattr(layers, "ROOT", tmp_path)
    original = layers.revision()
    script.write_text("# modified layer\n")
    changed = layers.revision()
    assert changed != original

    # Generated files and controller implementation are not layer source identities.
    for directory in (".build", "engine"):
        ignored = tmp_path / directory / "example.py"
        ignored.parent.mkdir()
        ignored.write_text("# unrelated to the exported layer\n")
    assert layers.revision() == changed


@pytest.mark.parametrize("case",catalog()["cases"],ids=lambda c:c["id"])
def test_independent_reference(case):
    torch=pytest.importorskip("torch")
    torch.set_num_threads(1)
    inputs,weights=fixtures(case)
    with torch.inference_mode():actual=module(case,weights)(*[torch.from_numpy(x) for x in inputs]).numpy()
    assert correctness(actual,reference(case,inputs,weights))["passed"]


def test_fixtures_are_order_independent():
    c=catalog()["cases"][0]
    before=fixtures(c)
    fixtures(catalog()["cases"][-1])
    after=fixtures(c)
    np.testing.assert_array_equal(before[0][0],after[0][0])
    np.testing.assert_array_equal(before[1]["weight"],after[1]["weight"])


def test_reference_math_and_mask():
    actual=reference({"operation":"linear"},(np.array([[1,2,3]]),),{"weight":np.array([[2,-1,3]]),"bias":np.array([4])})
    np.testing.assert_array_equal(actual,[[13]])
    q,k=np.zeros((1,1,1,2)),np.zeros((1,1,2,2))
    value=np.array([[[[3,4],[100,200]]]])
    actual=reference({"operation":"attention"},(q,k,value,np.array([[[[0,-10000]]]])),{})
    np.testing.assert_array_equal(actual,[[[[3,4]]]])


@pytest.mark.parametrize("actual,expected",[(np.array([np.nan]),np.ones(1)),(np.ones(1),np.array([np.inf])),(np.ones((1,2)),np.ones((2,1))),(np.ones(1),np.zeros(1))])
def test_invalid_outputs_fail(actual,expected):
    assert not correctness(actual,expected)["passed"]


def test_export_preserves_unsupported_reason(tmp_path,monkeypatch):
    pytest.importorskip("torch")
    from engine.workloads import export
    spec=catalog();spec["cases"]=spec["cases"][:1]
    monkeypatch.setattr(export,"catalog",lambda *args:spec)
    def unsupported(*args):raise ValueError("not fully delegated")
    monkeypatch.setattr(export,"export_program",unsupported)
    report=export.export_suite(tmp_path)
    assert report["cases"][0]["export_status"]=="unsupported"
    assert "not fully delegated" in report["cases"][0]["reason"]
    assert not list(tmp_path.glob("*.pte"))
