"""PyTorch lowering for the ExecuTorch Vulkan adapter."""

import operator


def export_program(layer, inputs, destination):
    import torch
    from executorch.exir import to_edge_transform_and_lower
    from executorch.exir.delegate import executorch_call_delegate
    from executorch.backends.vulkan.partitioner.vulkan_partitioner import VulkanPartitioner

    edge = to_edge_transform_and_lower(
        torch.export.export(layer, inputs, strict=True),
        partitioner=[VulkanPartitioner({"force_fp16": False})],
    )
    graph = edge.exported_program().graph
    calls = [n for n in graph.nodes if n.op == "call_function"]
    delegates = [n for n in calls if n.target == executorch_call_delegate]
    leftovers = [
        str(n.target) for n in calls if n.target not in (executorch_call_delegate, operator.getitem)
    ]
    for node in delegates:
        lowered = getattr(edge.exported_program().graph_module, node.args[0].target)
        if lowered.backend_id != "VulkanBackend":
            leftovers.append(lowered.backend_id)
    coverage = dict(
        delegate_count=len(delegates),
        non_delegate_ops=leftovers,
        fully_delegated=bool(delegates) and not leftovers,
    )
    destination.with_suffix(".graph.txt").write_text(str(graph))
    if not coverage["fully_delegated"]:
        raise ValueError("Not fully Vulkan delegated: " + str(coverage))
    destination.write_bytes(edge.to_executorch().buffer)
    return coverage
