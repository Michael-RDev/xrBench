#include "capabilities.h"
#include "vulkan_dispatch.h"
#include <vector>
#include <stdexcept>
namespace xrgpu {
using Json=nlohmann::json;
Json capabilities() {
  Json out={{"vulkan",false},{"gpu_timestamps",false},{"runtime_revision",XRGPU_ET_REVISION},
    {"runner_revision",XRGPU_RUNNER_REVISION},{"precision","fp32"},{"variant","stock"},
    {"backends",Json::object()}};
  VkInstance instance{};
  try {
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;
    if(xrgpu::vulkan_dispatch().vkCreateInstance(&info,nullptr,&instance)!=VK_SUCCESS)throw std::runtime_error("Vulkan 1.1 instance unavailable");
    uint32_t n=0;if(xrgpu::vulkan_dispatch().vkEnumeratePhysicalDevices(instance,&n,nullptr)!=VK_SUCCESS||!n)throw std::runtime_error("No Vulkan physical device");
    std::vector<VkPhysicalDevice> devices(n);if(xrgpu::vulkan_dispatch().vkEnumeratePhysicalDevices(instance,&n,devices.data())!=VK_SUCCESS)throw std::runtime_error("Device enumeration failed");
    // Do not infer cross-backend device selection on multi-GPU systems.
    const bool singleDevice=devices.size()==1;out["physical_device_count"]=devices.size();
    auto physical=devices[0];VkPhysicalDeviceProperties p{};xrgpu::vulkan_dispatch().vkGetPhysicalDeviceProperties(physical,&p);
    VkPhysicalDeviceFeatures f{};xrgpu::vulkan_dispatch().vkGetPhysicalDeviceFeatures(physical,&f);
    xrgpu::vulkan_dispatch().vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);
    std::vector<VkQueueFamilyProperties> queues(n);xrgpu::vulkan_dispatch().vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,queues.data());
    int family=-1;for(uint32_t i=0;i<n;i++)if(queues[i].queueCount&&(queues[i].queueFlags&VK_QUEUE_COMPUTE_BIT)){family=i;break;}
    Json extensions=Json::array();n=0;
    if(xrgpu::vulkan_dispatch().vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,nullptr)==VK_SUCCESS){
      std::vector<VkExtensionProperties> e(n);
      if(xrgpu::vulkan_dispatch().vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,e.data())==VK_SUCCESS)for(auto& x:e)extensions.push_back(x.extensionName);
    }
    VkImageFormatProperties image{};
    bool texture=xrgpu::vulkan_dispatch().vkGetPhysicalDeviceImageFormatProperties(physical,VK_FORMAT_R32G32B32A32_SFLOAT,VK_IMAGE_TYPE_3D,
      VK_IMAGE_TILING_OPTIMAL,VK_IMAGE_USAGE_STORAGE_BIT|VK_IMAGE_USAGE_SAMPLED_BIT,0,&image)==VK_SUCCESS;
    bool timestamps=family>=0&&queues[family].timestampValidBits>0&&p.limits.timestampPeriod>0;
    out.update({{"vulkan",true},{"gpu",p.deviceName},{"vendor_id",p.vendorID},{"device_id",p.deviceID},
      {"physical_device_index",0},{"driver",p.driverVersion},{"api_version",p.apiVersion},{"compute_queue_family",family},
      {"gpu_timestamps",timestamps},{"timestamp_valid_bits",family<0?0:queues[family].timestampValidBits},
      {"timestamp_period_ns",p.limits.timestampPeriod},{"max_workgroup_invocations",p.limits.maxComputeWorkGroupInvocations},
      {"max_workgroup_size",{p.limits.maxComputeWorkGroupSize[0],p.limits.maxComputeWorkGroupSize[1],p.limits.maxComputeWorkGroupSize[2]}},
      {"max_workgroup_count",{p.limits.maxComputeWorkGroupCount[0],p.limits.maxComputeWorkGroupCount[1],p.limits.maxComputeWorkGroupCount[2]}},
      {"max_shared_memory_bytes",p.limits.maxComputeSharedMemorySize},{"max_storage_buffer_bytes",p.limits.maxStorageBufferRange},
      {"extensions",extensions},{"shader_int64",bool(f.shaderInt64)},{"shader_int16",bool(f.shaderInt16)},
      {"fp32_storage_image_3d",texture}});
    bool common=singleDevice&&p.apiVersion>=VK_API_VERSION_1_1&&family>=0;
    for(const auto& name:{"executorch_vulkan","vulkan_resident"}){
      bool resident=std::string(name)=="vulkan_resident";
      bool compatible=common&&(resident?(p.limits.maxComputeWorkGroupInvocations>=64&&p.limits.maxComputeWorkGroupSize[0]>=64):texture);
      out["backends"][name]={{"compatible",compatible},{"status",compatible?"compatible_unvalidated":"unsupported"},
        {"reason",compatible?"Capability requirements met; workload correctness and performance still require testing":
          "Required Vulkan version, single physical device, compute queue, texture format or workgroup limits unavailable"}};
    }
  } catch(const std::exception& e) {out["reason"]=e.what();}
  if(instance)xrgpu::vulkan_dispatch().vkDestroyInstance(instance,nullptr);
  for(const auto& name:{"executorch_vulkan","vulkan_resident"})if(!out["backends"].contains(name))
    out["backends"][name]={{"compatible",false},{"status","unsupported"},{"reason",out.value("reason","Vulkan unavailable")}};
  return out;
}
}
