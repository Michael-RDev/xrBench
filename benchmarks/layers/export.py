import argparse
import hashlib
import importlib.metadata
import json
import operator
from pathlib import Path

from benchmarks.layers import catalog, correctness, fixtures, module, reference, revision
from benchmarks.layers.engine.reports import save


def export_program(layer, inputs, destination):
    import torch
    from executorch.exir import to_edge_transform_and_lower
    from executorch.exir.delegate import executorch_call_delegate
    from executorch.backends.vulkan.partitioner.vulkan_partitioner import VulkanPartitioner
    edge = to_edge_transform_and_lower(torch.export.export(layer, inputs, strict=True),
                                      partitioner=[VulkanPartitioner({"force_fp16": False})])
    graph = edge.exported_program().graph
    calls = [n for n in graph.nodes if n.op == "call_function"]
    delegates = [n for n in calls if n.target == executorch_call_delegate]
    leftovers = [str(n.target) for n in calls if n.target not in (executorch_call_delegate, operator.getitem)]
    for node in delegates:
        lowered = getattr(edge.exported_program().graph_module, node.args[0].target)
        if lowered.backend_id != "VulkanBackend": leftovers.append(lowered.backend_id)
    coverage = dict(delegate_count=len(delegates), non_delegate_ops=leftovers,
                    fully_delegated=bool(delegates) and not leftovers)
    destination.with_suffix(".graph.txt").write_text(str(graph))
    if not coverage["fully_delegated"]:
        raise ValueError("Not fully Vulkan delegated: " + str(coverage))
    destination.write_bytes(edge.to_executorch().buffer)
    return coverage


def export_suite(output):
    import torch
    torch.set_num_threads(1)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    spec = catalog()
    suite = dict(schema_version=2, seed=spec["seed"], precision="fp32", suite_revision=revision(),
                 atol=spec["atol"], rtol=spec["rtol"], reference="numpy_float64_rounded_to_fp32",
                 environment={k: importlib.metadata.version(k) for k in ("torch", "executorch", "numpy")}, cases=[])
    for case in spec["cases"]:
        print("Export " + case["id"], flush=True)
        row = json.loads(json.dumps(case))
        inputs, weights = fixtures(case)
        expected = reference(case, inputs, weights)
        layer = module(case, weights)
        tensors = tuple(torch.from_numpy(x) for x in inputs)
        with torch.inference_mode():
            check = correctness(layer(*tensors).numpy(), expected)
        if not check["passed"]: raise ValueError(f"Reference mismatch: {case['id']}: {check}")
        row.update(output_shape=list(expected.shape), expected=case["id"] + ".expected.bin")
        expected.astype("<f4").tofile(output / row["expected"])
        for i, (value, entry) in enumerate(zip(inputs, row["inputs"])):
            entry["file"] = f"{case['id']}.input{i}.bin"
            value.astype("<i8" if entry["dtype"] == "int64" else "<f4").tofile(output / entry["file"])
        target = output / (case["id"] + ".pte")
        target.unlink(missing_ok=True)
        try:
            row["coverage"] = export_program(layer, tensors, target)
            row.update(export_status="ready", program=target.name)
        except Exception as error:
            target.unlink(missing_ok=True)
            row.update(export_status="unsupported", reason=f"{type(error).__name__}: {error}")
        suite["cases"].append(row)
    save(output / "suite.json", suite)
    paths = {"suite.json"}
    for case in suite["cases"]:
        paths.add(case["expected"])
        paths.update(x["file"] for x in case["inputs"])
        if case["export_status"] == "ready": paths.add(case["program"])
    save(output / "manifest.json", {"suite_revision":suite["suite_revision"], "files":{
        name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in sorted(paths)}})
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Export the standalone native layer bundle with strict Vulkan coverage checks.')
    parser.add_argument("--output",type=Path, default=Path(__file__).parent/".build/bundle")
    args = parser.parse_args()
    suite = export_suite(args.output)
    print(f"Exported {sum(c['export_status']=='ready' for c in suite['cases'])}/{len(suite['cases'])} cases")
