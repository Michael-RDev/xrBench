#include <vulkan/vulkan.h>
#include <jni.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace {
using Json=nlohmann::json;
using Clock=std::chrono::steady_clock;
constexpr VkDeviceSize bytes=16*1024*1024; // Two buffers, 32 MiB total.
const uint32_t compute_code[]=
#include "compute.inc"
;
const uint32_t memory_code[]=
#include "memory.inc"
;
const uint32_t dispatch_code[]=
#include "dispatch.inc"
;
void checked(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Calibration Vulkan operation failed: "+std::to_string(r));}
struct Calibration {
 JNIEnv* env;jobject guard;jmethodID canceled;
 Clock::time_point started=Clock::now(),deadline=started+std::chrono::seconds(15);
 VkInstance instance{};VkDevice device{};VkPhysicalDevice physical{};VkQueue queue{};uint32_t family=0,bits=0;
 VkPhysicalDeviceProperties properties{};
 VkBuffer buffers[2]{};VkDeviceMemory memory[2]{};void* mapped[2]{};
 VkDescriptorSetLayout descriptors{};VkDescriptorPool pool{};VkDescriptorSet set{};VkPipelineLayout layout{};
 VkCommandPool commands{};VkCommandBuffer command{};VkQueryPool queries{};VkFence fence{};
 std::vector<VkPipeline> pipelines;
 Calibration(JNIEnv* e,jobject g):env(e),guard(g){canceled=env->GetMethodID(env->GetObjectClass(g),"getAsBoolean","()Z");}
 void check(){
   if(Clock::now()>=deadline)throw std::runtime_error("Calibration reached the 15-second limit");
   if(env->CallBooleanMethod(guard,canceled)||env->ExceptionCheck())throw std::runtime_error("Calibration canceled or thermal conditions changed");
 }
 ~Calibration(){
   if(device){
     vkDeviceWaitIdle(device);
     for(auto p:pipelines)vkDestroyPipeline(device,p,nullptr);
     if(fence)vkDestroyFence(device,fence,nullptr);
     if(queries)vkDestroyQueryPool(device,queries,nullptr);
     if(commands)vkDestroyCommandPool(device,commands,nullptr);
     if(layout)vkDestroyPipelineLayout(device,layout,nullptr);
     if(pool)vkDestroyDescriptorPool(device,pool,nullptr);
     if(descriptors)vkDestroyDescriptorSetLayout(device,descriptors,nullptr);
     for(int i=0;i<2;i++){if(mapped[i])vkUnmapMemory(device,memory[i]);if(buffers[i])vkDestroyBuffer(device,buffers[i],nullptr);if(memory[i])vkFreeMemory(device,memory[i],nullptr);}
     vkDestroyDevice(device,nullptr);
   }
   if(instance)vkDestroyInstance(instance,nullptr);
 }
 void initialize(){
   check();VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
   VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;checked(vkCreateInstance(&info,nullptr,&instance));
   uint32_t n=0;checked(vkEnumeratePhysicalDevices(instance,&n,nullptr));if(!n)throw std::runtime_error("No Vulkan device");
   std::vector<VkPhysicalDevice> devices(n);checked(vkEnumeratePhysicalDevices(instance,&n,devices.data()));physical=devices[0];
   vkGetPhysicalDeviceProperties(physical,&properties);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);
   std::vector<VkQueueFamilyProperties> families(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,families.data());
   bool found=false;for(uint32_t i=0;i<n;i++)if(families[i].queueFlags&VK_QUEUE_COMPUTE_BIT){family=i;bits=families[i].timestampValidBits;found=true;break;}
   if(!found||!bits||properties.limits.timestampPeriod<=0)throw std::runtime_error("GPU timestamps unavailable");
   if(properties.limits.maxComputeWorkGroupInvocations<128||properties.limits.maxComputeWorkGroupSize[0]<128||properties.limits.maxComputeWorkGroupCount[0]<32768)throw std::runtime_error("Calibration dispatch limits unavailable");
   float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
   VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;checked(vkCreateDevice(physical,&di,nullptr,&device));vkGetDeviceQueue(device,family,0,&queue);
   VkPhysicalDeviceMemoryProperties mp{};vkGetPhysicalDeviceMemoryProperties(physical,&mp);
   VkDeviceSize allocated=0;
   for(int i=0;i<2;i++){
     VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=bytes;bi.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
     checked(vkCreateBuffer(device,&bi,nullptr,&buffers[i]));VkMemoryRequirements req{};vkGetBufferMemoryRequirements(device,buffers[i],&req);
     allocated+=req.size;if(allocated>64*1024*1024)throw std::runtime_error("Calibration scratch exceeds 64 MiB");
     uint32_t chosen=UINT32_MAX;
     for(uint32_t m=0;m<mp.memoryTypeCount;m++)if((req.memoryTypeBits&(1u<<m))&&
       (mp.memoryTypes[m].propertyFlags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)){
       chosen=m;if(mp.memoryTypes[m].propertyFlags&VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)break;
     }
     if(chosen==UINT32_MAX)throw std::runtime_error("Host-coherent calibration memory unavailable");
     VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=req.size;ai.memoryTypeIndex=chosen;
     checked(vkAllocateMemory(device,&ai,nullptr,&memory[i]));checked(vkBindBufferMemory(device,buffers[i],memory[i],0));checked(vkMapMemory(device,memory[i],0,bytes,0,&mapped[i]));
   }
   std::fill_n(static_cast<float*>(mapped[0]),bytes/4,1.f);std::fill_n(static_cast<float*>(mapped[1]),bytes/4,0.f);
   VkDescriptorSetLayoutBinding bindings[2]{};for(int i=0;i<2;i++){bindings[i].binding=i;bindings[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;bindings[i].descriptorCount=1;bindings[i].stageFlags=VK_SHADER_STAGE_COMPUTE_BIT;}
   VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=2;li.pBindings=bindings;checked(vkCreateDescriptorSetLayout(device,&li,nullptr,&descriptors));
   VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,2};VkDescriptorPoolCreateInfo pi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};pi.maxSets=1;pi.poolSizeCount=1;pi.pPoolSizes=&size;checked(vkCreateDescriptorPool(device,&pi,nullptr,&pool));
   VkDescriptorSetAllocateInfo si{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};si.descriptorPool=pool;si.descriptorSetCount=1;si.pSetLayouts=&descriptors;checked(vkAllocateDescriptorSets(device,&si,&set));
   VkDescriptorBufferInfo bufferInfo[2]{};VkWriteDescriptorSet writes[2]{};
   for(int i=0;i<2;i++){bufferInfo[i]={buffers[i],0,bytes};writes[i]={VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};writes[i].dstSet=set;writes[i].dstBinding=i;writes[i].descriptorCount=1;writes[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;writes[i].pBufferInfo=&bufferInfo[i];}
   vkUpdateDescriptorSets(device,2,writes,0,nullptr);
   VkPushConstantRange push{VK_SHADER_STAGE_COMPUTE_BIT,0,4};VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pli.setLayoutCount=1;pli.pSetLayouts=&descriptors;pli.pushConstantRangeCount=1;pli.pPushConstantRanges=&push;checked(vkCreatePipelineLayout(device,&pli,nullptr,&layout));
   VkCommandPoolCreateInfo ci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};ci.queueFamilyIndex=family;ci.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;checked(vkCreateCommandPool(device,&ci,nullptr,&commands));
   VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=commands;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;checked(vkAllocateCommandBuffers(device,&ca,&command));
   VkQueryPoolCreateInfo q{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};q.queryType=VK_QUERY_TYPE_TIMESTAMP;q.queryCount=2;checked(vkCreateQueryPool(device,&q,nullptr,&queries));
   VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};checked(vkCreateFence(device,&fi,nullptr,&fence));check();
 }
 VkPipeline pipeline(const uint32_t* code,size_t length){
   check();VkShaderModule shader{};VkShaderModuleCreateInfo s{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};s.codeSize=length;s.pCode=code;checked(vkCreateShaderModule(device,&s,nullptr,&shader));
   VkComputePipelineCreateInfo p{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};p.layout=layout;p.stage={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};p.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT;p.stage.module=shader;p.stage.pName="main";
   VkPipeline result{};auto status=vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&p,nullptr,&result);vkDestroyShaderModule(device,shader,nullptr);checked(status);pipelines.push_back(result);return result;
 }
 double sample(VkPipeline pipeline,uint32_t groups){
   check();checked(vkResetFences(device,1,&fence));checked(vkResetCommandBuffer(command,0));VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};checked(vkBeginCommandBuffer(command,&begin));
   vkCmdResetQueryPool(command,queries,0,2);
   VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT|VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT;
   vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_HOST_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&barrier,0,nullptr,0,nullptr);
   vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,&set,0,nullptr);
   uint32_t iterations=1024;vkCmdPushConstants(command,layout,VK_SHADER_STAGE_COMPUTE_BIT,0,4,&iterations);
   vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,queries,0);vkCmdDispatch(command,groups,1,1);vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,queries,1);
   barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
   vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);checked(vkEndCommandBuffer(command));
   VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;checked(vkQueueSubmit(queue,1,&submit,fence));
   auto remaining=std::chrono::duration_cast<std::chrono::nanoseconds>(deadline-Clock::now()).count();
   checked(vkWaitForFences(device,1,&fence,VK_TRUE,std::max<int64_t>(1,remaining)));check();
   uint64_t times[2]{};checked(vkGetQueryPoolResults(device,queries,0,2,sizeof(times),times,sizeof(uint64_t),VK_QUERY_RESULT_64_BIT));
   const uint64_t mask=bits==64?UINT64_MAX:((uint64_t(1)<<bits)-1);
   double ms=double((times[1]-times[0])&mask)*properties.limits.timestampPeriod/1e6;
   if(!(ms>0&&std::isfinite(ms)))throw std::runtime_error("Invalid calibration timestamp interval");return ms;
 }
 Json measure(VkPipeline p,uint32_t groups,size_t checkedElements){
   std::vector<double> timings;auto output=static_cast<float*>(mapped[1]);
   for(int iteration=0;iteration<12;iteration++){
     // Reset outside the timed interval so a missing dispatch cannot pass using stale output.
     std::fill_n(output,checkedElements,0.f);double elapsed=sample(p,groups);
     for(size_t i=0;i<checkedElements;i++)if(!std::isfinite(output[i])||std::abs(output[i]-1.f)>1e-4f)throw std::runtime_error("Calibration output validation failed");
     if(iteration>=3)timings.push_back(elapsed);
   }
   auto sorted=timings;std::sort(sorted.begin(),sorted.end());check();return Json{{"samples_ms",timings},{"median_ms",sorted[4]}};
 }
 Json run(){
   initialize();auto cp=pipeline(compute_code,sizeof(compute_code)),mp=pipeline(memory_code,sizeof(memory_code)),dp=pipeline(dispatch_code,sizeof(dispatch_code));
   auto compute=measure(cp,512,65536*8),memory=measure(mp,32768,bytes/4),dispatch=measure(dp,1,1);
   return Json{{"available",true},{"version",1},{"compute_gflops",(65536.0*8*2*1024)/(compute["median_ms"].get<double>()*1e6)},
     {"memory_gbps",double(bytes*2)/(memory["median_ms"].get<double>()*1e6)},{"dispatch_ms",dispatch["median_ms"]},
     {"compute",compute},{"memory",memory},{"dispatch",dispatch},{"scratch_bytes",bytes*2},
     {"wall_ms",std::chrono::duration<double,std::milli>(Clock::now()-started).count()},
     {"reference_scope","FP32 FMA, 16 MiB host-coherent streaming copy, one dispatch; empirical references, not hardware peaks"},
     {"gpu",properties.deviceName},{"driver",properties.driverVersion},{"precision","fp32"}};
 }
};
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_calibrate(JNIEnv* env,jclass,jobject guard){
 Json result;try{Calibration calibration(env,guard);result=calibration.run();}catch(const std::exception& e){result={{"version",1},{"available",false},{"reason",e.what()}};}
 return env->NewStringUTF(result.dump().c_str());
}
