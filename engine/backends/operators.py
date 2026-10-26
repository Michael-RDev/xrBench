"""Report identity for the standalone typed Vulkan operator runner.

This backend is deliberately absent from the APK controller's selectable list:
its typed byte tensors are not compatible with the legacy FP32 layer JNI API.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorBackend:
    id: str = "xrgpu_vulkan"
    label: str = "xrGPU native Vulkan operators"
    resident: bool = False

    def check_request(self, config, cases):
        raise ValueError("Typed native operators use the website's native suite, separately from the legacy FP32 model controller")

