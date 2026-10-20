"""The Android Vulkan adapters. No model-name allowlist for isolated computation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VulkanBackend:
    resident: bool = False

    @property
    def id(self):
        return "vulkan_resident" if self.resident else "executorch_vulkan"

    @property
    def label(self):
        return "Vulkan resident matmul" if self.resident else "ExecuTorch Vulkan layers"

    def check_request(self, config, cases):
        if self.resident and (
            config["mode"] != "isolated"
            or config["preset"] == "sustained"
            or any(c["operation"] != "matmul" for c in cases)
        ):
            raise ValueError("Resident Vulkan supports isolated matmul with Smoke or Standard")
        control = config.get("validation_control", "none")
        if control not in ("none", "incorrect", "slower"):
            raise ValueError("Unknown validation control")
        if control != "none" and (config.get("experiment") != "candidate"):
            raise ValueError("Validation controls require a stock/candidate matmul experiment")

    def check_device(self, device, config):
        if config["mode"] == "xr" and (
            device.get("model") != "Quest 3"
            or config.get("execution_target", "unknown") not in ("unknown", "headset")
        ):
            raise ValueError(
                "The optional XR scene currently targets Quest 3 only; use Native layers for this device"
            )

    def install(self, transport, serial, build, metadata):
        transport.install(serial, build / "xrgpu-benchmarks.apk", metadata["apk_sha256"])

    def launch(self, transport, serial, config):
        transport.start(serial, config)

    def export(self, layer, inputs, destination):
        if self.resident:
            raise ValueError("Resident matmul uses shared tensor fixtures, not exported programs")
        from .vulkan_export import export_program

        return export_program(layer, inputs, destination)
