#pragma once
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdexcept>
#include <string>

namespace xrgpu {
// ExecuTorch embeds Volk variables named vkCreateInstance, etc. Calling the
// Vulkan C prototypes from this same shared object can resolve to those data
// symbols. Resolve the platform loader explicitly and keep this table private;
// probing/calibration must also not replace Volk's active instance/device table.
class VulkanDispatch {
  void* library_ = nullptr;
  template<class T> T resolve(const char* name) {
    auto result = reinterpret_cast<T>(dlsym(library_, name));
    if (!result) throw std::runtime_error(std::string("Vulkan entry point unavailable: ") + name);
    return result;
  }
 public:
  PFN_vkAllocateCommandBuffers vkAllocateCommandBuffers{};
  PFN_vkAllocateDescriptorSets vkAllocateDescriptorSets{};
  PFN_vkAllocateMemory vkAllocateMemory{};
  PFN_vkBeginCommandBuffer vkBeginCommandBuffer{};
  PFN_vkBindBufferMemory vkBindBufferMemory{};
  PFN_vkCmdBindDescriptorSets vkCmdBindDescriptorSets{};
  PFN_vkCmdBindPipeline vkCmdBindPipeline{};
  PFN_vkCmdDispatch vkCmdDispatch{};
  PFN_vkCmdPipelineBarrier vkCmdPipelineBarrier{};
  PFN_vkCmdPushConstants vkCmdPushConstants{};
  PFN_vkCmdResetQueryPool vkCmdResetQueryPool{};
  PFN_vkCmdWriteTimestamp vkCmdWriteTimestamp{};
  PFN_vkCreateBuffer vkCreateBuffer{};
  PFN_vkCreateCommandPool vkCreateCommandPool{};
  PFN_vkCreateComputePipelines vkCreateComputePipelines{};
  PFN_vkCreateDescriptorPool vkCreateDescriptorPool{};
  PFN_vkCreateDescriptorSetLayout vkCreateDescriptorSetLayout{};
  PFN_vkCreateDevice vkCreateDevice{};
  PFN_vkCreateFence vkCreateFence{};
  PFN_vkCreateInstance vkCreateInstance{};
  PFN_vkCreatePipelineLayout vkCreatePipelineLayout{};
  PFN_vkCreateQueryPool vkCreateQueryPool{};
  PFN_vkCreateShaderModule vkCreateShaderModule{};
  PFN_vkDestroyBuffer vkDestroyBuffer{};
  PFN_vkDestroyCommandPool vkDestroyCommandPool{};
  PFN_vkDestroyDescriptorPool vkDestroyDescriptorPool{};
  PFN_vkDestroyDescriptorSetLayout vkDestroyDescriptorSetLayout{};
  PFN_vkDestroyDevice vkDestroyDevice{};
  PFN_vkDestroyFence vkDestroyFence{};
  PFN_vkDestroyInstance vkDestroyInstance{};
  PFN_vkDestroyPipeline vkDestroyPipeline{};
  PFN_vkDestroyPipelineLayout vkDestroyPipelineLayout{};
  PFN_vkDestroyQueryPool vkDestroyQueryPool{};
  PFN_vkDestroyShaderModule vkDestroyShaderModule{};
  PFN_vkDeviceWaitIdle vkDeviceWaitIdle{};
  PFN_vkEndCommandBuffer vkEndCommandBuffer{};
  PFN_vkEnumerateDeviceExtensionProperties vkEnumerateDeviceExtensionProperties{};
  PFN_vkEnumeratePhysicalDevices vkEnumeratePhysicalDevices{};
  PFN_vkFreeMemory vkFreeMemory{};
  PFN_vkGetBufferMemoryRequirements vkGetBufferMemoryRequirements{};
  PFN_vkGetDeviceQueue vkGetDeviceQueue{};
  PFN_vkGetPhysicalDeviceFeatures vkGetPhysicalDeviceFeatures{};
  PFN_vkGetPhysicalDeviceImageFormatProperties vkGetPhysicalDeviceImageFormatProperties{};
  PFN_vkGetPhysicalDeviceMemoryProperties vkGetPhysicalDeviceMemoryProperties{};
  PFN_vkGetPhysicalDeviceProperties vkGetPhysicalDeviceProperties{};
  PFN_vkGetPhysicalDeviceQueueFamilyProperties vkGetPhysicalDeviceQueueFamilyProperties{};
  PFN_vkGetQueryPoolResults vkGetQueryPoolResults{};
  PFN_vkMapMemory vkMapMemory{};
  PFN_vkQueueSubmit vkQueueSubmit{};
  PFN_vkResetCommandBuffer vkResetCommandBuffer{};
  PFN_vkResetFences vkResetFences{};
  PFN_vkUnmapMemory vkUnmapMemory{};
  PFN_vkUpdateDescriptorSets vkUpdateDescriptorSets{};
  PFN_vkWaitForFences vkWaitForFences{};
  VulkanDispatch() {
    library_ = dlopen("libvulkan.so", RTLD_NOW | RTLD_LOCAL);
    if (!library_) throw std::runtime_error("Android Vulkan loader unavailable");
    try {
      vkAllocateCommandBuffers = resolve<PFN_vkAllocateCommandBuffers>("vkAllocateCommandBuffers");
      vkAllocateDescriptorSets = resolve<PFN_vkAllocateDescriptorSets>("vkAllocateDescriptorSets");
      vkAllocateMemory = resolve<PFN_vkAllocateMemory>("vkAllocateMemory");
      vkBeginCommandBuffer = resolve<PFN_vkBeginCommandBuffer>("vkBeginCommandBuffer");
      vkBindBufferMemory = resolve<PFN_vkBindBufferMemory>("vkBindBufferMemory");
      vkCmdBindDescriptorSets = resolve<PFN_vkCmdBindDescriptorSets>("vkCmdBindDescriptorSets");
      vkCmdBindPipeline = resolve<PFN_vkCmdBindPipeline>("vkCmdBindPipeline");
      vkCmdDispatch = resolve<PFN_vkCmdDispatch>("vkCmdDispatch");
      vkCmdPipelineBarrier = resolve<PFN_vkCmdPipelineBarrier>("vkCmdPipelineBarrier");
      vkCmdPushConstants = resolve<PFN_vkCmdPushConstants>("vkCmdPushConstants");
      vkCmdResetQueryPool = resolve<PFN_vkCmdResetQueryPool>("vkCmdResetQueryPool");
      vkCmdWriteTimestamp = resolve<PFN_vkCmdWriteTimestamp>("vkCmdWriteTimestamp");
      vkCreateBuffer = resolve<PFN_vkCreateBuffer>("vkCreateBuffer");
      vkCreateCommandPool = resolve<PFN_vkCreateCommandPool>("vkCreateCommandPool");
      vkCreateComputePipelines = resolve<PFN_vkCreateComputePipelines>("vkCreateComputePipelines");
      vkCreateDescriptorPool = resolve<PFN_vkCreateDescriptorPool>("vkCreateDescriptorPool");
      vkCreateDescriptorSetLayout = resolve<PFN_vkCreateDescriptorSetLayout>("vkCreateDescriptorSetLayout");
      vkCreateDevice = resolve<PFN_vkCreateDevice>("vkCreateDevice");
      vkCreateFence = resolve<PFN_vkCreateFence>("vkCreateFence");
      vkCreateInstance = resolve<PFN_vkCreateInstance>("vkCreateInstance");
      vkCreatePipelineLayout = resolve<PFN_vkCreatePipelineLayout>("vkCreatePipelineLayout");
      vkCreateQueryPool = resolve<PFN_vkCreateQueryPool>("vkCreateQueryPool");
      vkCreateShaderModule = resolve<PFN_vkCreateShaderModule>("vkCreateShaderModule");
      vkDestroyBuffer = resolve<PFN_vkDestroyBuffer>("vkDestroyBuffer");
      vkDestroyCommandPool = resolve<PFN_vkDestroyCommandPool>("vkDestroyCommandPool");
      vkDestroyDescriptorPool = resolve<PFN_vkDestroyDescriptorPool>("vkDestroyDescriptorPool");
      vkDestroyDescriptorSetLayout = resolve<PFN_vkDestroyDescriptorSetLayout>("vkDestroyDescriptorSetLayout");
      vkDestroyDevice = resolve<PFN_vkDestroyDevice>("vkDestroyDevice");
      vkDestroyFence = resolve<PFN_vkDestroyFence>("vkDestroyFence");
      vkDestroyInstance = resolve<PFN_vkDestroyInstance>("vkDestroyInstance");
      vkDestroyPipeline = resolve<PFN_vkDestroyPipeline>("vkDestroyPipeline");
      vkDestroyPipelineLayout = resolve<PFN_vkDestroyPipelineLayout>("vkDestroyPipelineLayout");
      vkDestroyQueryPool = resolve<PFN_vkDestroyQueryPool>("vkDestroyQueryPool");
      vkDestroyShaderModule = resolve<PFN_vkDestroyShaderModule>("vkDestroyShaderModule");
      vkDeviceWaitIdle = resolve<PFN_vkDeviceWaitIdle>("vkDeviceWaitIdle");
      vkEndCommandBuffer = resolve<PFN_vkEndCommandBuffer>("vkEndCommandBuffer");
      vkEnumerateDeviceExtensionProperties = resolve<PFN_vkEnumerateDeviceExtensionProperties>("vkEnumerateDeviceExtensionProperties");
      vkEnumeratePhysicalDevices = resolve<PFN_vkEnumeratePhysicalDevices>("vkEnumeratePhysicalDevices");
      vkFreeMemory = resolve<PFN_vkFreeMemory>("vkFreeMemory");
      vkGetBufferMemoryRequirements = resolve<PFN_vkGetBufferMemoryRequirements>("vkGetBufferMemoryRequirements");
      vkGetDeviceQueue = resolve<PFN_vkGetDeviceQueue>("vkGetDeviceQueue");
      vkGetPhysicalDeviceFeatures = resolve<PFN_vkGetPhysicalDeviceFeatures>("vkGetPhysicalDeviceFeatures");
      vkGetPhysicalDeviceImageFormatProperties = resolve<PFN_vkGetPhysicalDeviceImageFormatProperties>("vkGetPhysicalDeviceImageFormatProperties");
      vkGetPhysicalDeviceMemoryProperties = resolve<PFN_vkGetPhysicalDeviceMemoryProperties>("vkGetPhysicalDeviceMemoryProperties");
      vkGetPhysicalDeviceProperties = resolve<PFN_vkGetPhysicalDeviceProperties>("vkGetPhysicalDeviceProperties");
      vkGetPhysicalDeviceQueueFamilyProperties = resolve<PFN_vkGetPhysicalDeviceQueueFamilyProperties>("vkGetPhysicalDeviceQueueFamilyProperties");
      vkGetQueryPoolResults = resolve<PFN_vkGetQueryPoolResults>("vkGetQueryPoolResults");
      vkMapMemory = resolve<PFN_vkMapMemory>("vkMapMemory");
      vkQueueSubmit = resolve<PFN_vkQueueSubmit>("vkQueueSubmit");
      vkResetCommandBuffer = resolve<PFN_vkResetCommandBuffer>("vkResetCommandBuffer");
      vkResetFences = resolve<PFN_vkResetFences>("vkResetFences");
      vkUnmapMemory = resolve<PFN_vkUnmapMemory>("vkUnmapMemory");
      vkUpdateDescriptorSets = resolve<PFN_vkUpdateDescriptorSets>("vkUpdateDescriptorSets");
      vkWaitForFences = resolve<PFN_vkWaitForFences>("vkWaitForFences");
    } catch (...) {dlclose(library_); library_ = nullptr; throw;}
  }
  ~VulkanDispatch() {if (library_) dlclose(library_);}
  VulkanDispatch(const VulkanDispatch&) = delete;
  VulkanDispatch& operator=(const VulkanDispatch&) = delete;
};
inline const VulkanDispatch& vulkan_dispatch() {
  static const VulkanDispatch dispatch;
  return dispatch;
}
}
