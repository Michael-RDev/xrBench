"""Native CPU execution, independent of Vulkan capabilities."""
from dataclasses import dataclass
from .vulkan import VulkanBackend


@dataclass(frozen=True)
class CPUBackend(VulkanBackend):
    @property
    def id(self):
        return "cpu_resident" if self.resident else "executorch_cpu"

    @property
    def label(self):
        return "CPU resident matmul (scalar / NEON)" if self.resident else "ExecuTorch CPU layers"

    def check_request(self, config, cases):
        super().check_request(config, cases)
        if config["mode"] != "isolated":
            raise ValueError("CPU backends currently support isolated computation")
        if not self.resident and config.get("experiment", "none") != "none":
            raise ValueError("Choose CPU resident matmul for custom CPU kernel experiments")

    def check_device(self, device, config):
        if config["mode"] != "isolated":
            raise ValueError("CPU backends currently support isolated computation")

    def export(self, layer, inputs, destination):
        if self.resident:
            raise ValueError("Resident matmul uses shared fixtures")
        import torch
        from executorch.exir import to_edge_transform_and_lower
        program = to_edge_transform_and_lower(torch.export.export(layer, inputs, strict=True), partitioner=[])
        graph = program.exported_program().graph
        destination.with_suffix(".graph.txt").write_text(str(graph))
        destination.write_bytes(program.to_executorch().buffer)
        return dict(processor="cpu", delegate_count=0, fully_delegated=False,
                    kernels="ExecuTorch portable CPU", threading_policy="single calling thread")
