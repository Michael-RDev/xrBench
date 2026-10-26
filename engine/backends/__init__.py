"""Execution adapters; workload definitions and result analysis stay backend independent."""

from .vulkan import VulkanBackend
from .cpu import CPUBackend
from .operators import OperatorBackend

BACKENDS = {b.id: b for b in (VulkanBackend(), VulkanBackend(resident=True), CPUBackend(), CPUBackend(resident=True))}
TARGETS = ("unknown", "headset", "glasses", "phone", "compute_puck")


def get_backend(name="executorch_vulkan"):
    if name == "xrgpu_vulkan":
        return OperatorBackend()
    try:
        return BACKENDS[name]
    except (KeyError, TypeError):
        raise ValueError("Unknown execution backend") from None


def discovery(device):
    """Transport eligibility is deliberately weaker than native compatibility."""
    eligible = device.get("sdk", 0) >= 29 and "arm64-v8a" in device.get("abi", "").split(",")
    return dict(
        supported=eligible,
        support_status="probe_required" if eligible else "unsupported",
        support_label="Capability check required" if eligible else "Unsupported Android runtime",
        reason="Native capabilities and correctness are checked on the device before timing."
        if eligible
        else "The available runners require Android 10 or newer and arm64-v8a.",
        xr_available=device.get("model") == "Quest 3",
    )
