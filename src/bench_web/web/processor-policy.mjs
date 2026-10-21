// A missing/unsupported Vulkan backend is not evidence that a chip has no GPU.
// Keep this policy aligned with engine.devices.gpu_eligibility.
export function gpuEligibility(chip, capabilities = {}, conflict = false) {
  const detected = capabilities.vulkan === true || !!capabilities.gpu || capabilities.physical_device_count > 0 ||
    capabilities.backends?.executorch_vulkan?.compatible === true || capabilities.backends?.vulkan_resident?.compatible === true;
  const source = typeof chip?.gpu_presence_source === 'string' ? chip.gpu_presence_source.trim() : '';
  const declared = !conflict && ['present', 'absent'].includes(chip?.gpu_presence) ? chip.gpu_presence : 'unknown';
  const presence = detected ? 'present' : declared === 'absent' && !source ? 'unknown' : declared;
  return {gpu_presence: presence, gpu_presence_source: detected ? 'native' : presence === 'unknown' ? null : source || null,
    cpu_eligible: presence === 'absent' && !conflict};
}

export const CPU_RESTRICTION = 'CPU testing is available only for a chip confirmed to have no GPU. An unsupported GPU runner does not qualify.';
