#include "vulkan_dispatch.h"
#include <jni.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>
namespace {
using Json=nlohmann::json;using Clock=std::chrono::steady_clock;
const uint32_t stock_code[]=
#include "resident_matmul_tile1.inc"
;
const uint32_t candidate_code[]=
#include "resident_matmul_tile2.inc"
;
void checked(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Resident Vulkan operation failed: "+std::to_string(r));}
struct Resident {
 uint32_t dims[4]{};bool candidate=false,detailed=true;std::string control="none";
 VkDeviceSize sizes[3]{};uint32_t groups=0,repeats=1;double wall=0,gpu=0;Json events=Json::array();
 Clock::time_point deadline;bool deviceLocal=true;
 VkInstance instance{};VkDevice device{};VkPhysicalDevice physical{};VkQueue queue{};uint32_t family=0,bits=0;
 VkPhysicalDeviceProperties properties{};
 // Keep these buffers mapped for the model's lifetime; samples reuse the same allocations.
 VkBuffer buffers[3]{};VkDeviceMemory memory[3]{};void* mapped[3]{};
 VkDescriptorSetLayout descriptors{};VkDescriptorPool pool{};VkDescriptorSet set{};VkPipelineLayout layout{};
 VkCommandPool commands{};VkCommandBuffer command{};VkQueryPool queries{};VkFence fence{};
 std::vector<VkPipeline> pipelines;
 void check(){if(Clock::now()>=deadline)throw std::runtime_error("Resident submission exceeded 15 seconds");}
 ~Resident(){
   if(device){
     xrgpu::vulkan_dispatch().vkDeviceWaitIdle(device);
     for(auto p:pipelines)xrgpu::vulkan_dispatch().vkDestroyPipeline(device,p,nullptr);
     if(fence)xrgpu::vulkan_dispatch().vkDestroyFence(device,fence,nullptr);
     if(queries)xrgpu::vulkan_dispatch().vkDestroyQueryPool(device,queries,nullptr);
     if(commands)xrgpu::vulkan_dispatch().vkDestroyCommandPool(device,commands,nullptr);
     if(layout)xrgpu::vulkan_dispatch().vkDestroyPipelineLayout(device,layout,nullptr);
     if(pool)xrgpu::vulkan_dispatch().vkDestroyDescriptorPool(device,pool,nullptr);
     if(descriptors)xrgpu::vulkan_dispatch().vkDestroyDescriptorSetLayout(device,descriptors,nullptr);
     for(int i=0;i<3;i++){if(mapped[i])xrgpu::vulkan_dispatch().vkUnmapMemory(device,memory[i]);if(buffers[i])xrgpu::vulkan_dispatch().vkDestroyBuffer(device,buffers[i],nullptr);if(memory[i])xrgpu::vulkan_dispatch().vkFreeMemory(device,memory[i],nullptr);}
     xrgpu::vulkan_dispatch().vkDestroyDevice(device,nullptr);
   }
   if(instance)xrgpu::vulkan_dispatch().vkDestroyInstance(instance,nullptr);
 }
 void initialize(uint32_t m,uint32_t k,uint32_t columns,bool c,bool trace,const std::string& test){
   candidate=c;detailed=trace;control=test;repeats=candidate&&control=="slower"?8:1;
   dims[0]=m;dims[1]=k;dims[2]=columns;dims[3]=candidate&&control=="incorrect"?1:0;
   if(!m||!k||!columns||m>4096||k>4096||columns>4096)throw std::runtime_error("Resident matrix dimensions exceed limits");
   sizes[0]=uint64_t(m)*k*4;sizes[1]=uint64_t(k)*columns*4;sizes[2]=uint64_t(m)*columns*4;
   if(sizes[0]+sizes[1]+sizes[2]>64*1024*1024)throw std::runtime_error("Resident buffers exceed 64 MiB");
   uint32_t tile=candidate?2:1;groups=((m+tile-1)/tile*columns+63)/64;
   deadline=Clock::now()+std::chrono::seconds(15);check();VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
   VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;checked(xrgpu::vulkan_dispatch().vkCreateInstance(&info,nullptr,&instance));
   uint32_t n=0;checked(xrgpu::vulkan_dispatch().vkEnumeratePhysicalDevices(instance,&n,nullptr));if(!n)throw std::runtime_error("No Vulkan device");
   std::vector<VkPhysicalDevice> devices(n);checked(xrgpu::vulkan_dispatch().vkEnumeratePhysicalDevices(instance,&n,devices.data()));physical=devices[0];
   xrgpu::vulkan_dispatch().vkGetPhysicalDeviceProperties(physical,&properties);xrgpu::vulkan_dispatch().vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);
   std::vector<VkQueueFamilyProperties> families(n);xrgpu::vulkan_dispatch().vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,families.data());
   bool found=false;for(uint32_t i=0;i<n;i++)if(families[i].queueFlags&VK_QUEUE_COMPUTE_BIT){family=i;bits=families[i].timestampValidBits;found=true;break;}
   if(!found)throw std::runtime_error("Compute queue unavailable");
   detailed=detailed&&bits&&properties.limits.timestampPeriod>0;
   if(properties.limits.maxComputeWorkGroupInvocations<64||properties.limits.maxComputeWorkGroupSize[0]<64||properties.limits.maxComputeWorkGroupCount[0]<groups)throw std::runtime_error("Resident dispatch limits exceeded");
   float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
   VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;checked(xrgpu::vulkan_dispatch().vkCreateDevice(physical,&di,nullptr,&device));xrgpu::vulkan_dispatch().vkGetDeviceQueue(device,family,0,&queue);
   VkPhysicalDeviceMemoryProperties mp{};xrgpu::vulkan_dispatch().vkGetPhysicalDeviceMemoryProperties(physical,&mp);
   VkDeviceSize allocated=0;
   for(int i=0;i<3;i++){
     VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=sizes[i];
     if(sizes[i]>properties.limits.maxStorageBufferRange)throw std::runtime_error("Storage buffer range exceeded");bi.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
     checked(xrgpu::vulkan_dispatch().vkCreateBuffer(device,&bi,nullptr,&buffers[i]));VkMemoryRequirements req{};xrgpu::vulkan_dispatch().vkGetBufferMemoryRequirements(device,buffers[i],&req);
     allocated+=req.size;if(allocated>64*1024*1024)throw std::runtime_error("Resident scratch exceeds 64 MiB");
     uint32_t chosen=UINT32_MAX;
     for(uint32_t m=0;m<mp.memoryTypeCount;m++)if((req.memoryTypeBits&(1u<<m))&&
       (mp.memoryTypes[m].propertyFlags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)){
       chosen=m;if(mp.memoryTypes[m].propertyFlags&VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)break;
     }
     if(chosen==UINT32_MAX)throw std::runtime_error("Host-coherent resident memory unavailable");
     deviceLocal=deviceLocal&&bool(mp.memoryTypes[chosen].propertyFlags&VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
     VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=req.size;ai.memoryTypeIndex=chosen;
     checked(xrgpu::vulkan_dispatch().vkAllocateMemory(device,&ai,nullptr,&memory[i]));checked(xrgpu::vulkan_dispatch().vkBindBufferMemory(device,buffers[i],memory[i],0));checked(xrgpu::vulkan_dispatch().vkMapMemory(device,memory[i],0,sizes[i],0,&mapped[i]));
   }
   std::fill_n(static_cast<float*>(mapped[2]),sizes[2]/4,std::numeric_limits<float>::quiet_NaN());
   VkDescriptorSetLayoutBinding bindings[3]{};for(int i=0;i<3;i++){bindings[i].binding=i;bindings[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;bindings[i].descriptorCount=1;bindings[i].stageFlags=VK_SHADER_STAGE_COMPUTE_BIT;}
   VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=3;li.pBindings=bindings;checked(xrgpu::vulkan_dispatch().vkCreateDescriptorSetLayout(device,&li,nullptr,&descriptors));
   VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,3};VkDescriptorPoolCreateInfo pi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};pi.maxSets=1;pi.poolSizeCount=1;pi.pPoolSizes=&size;checked(xrgpu::vulkan_dispatch().vkCreateDescriptorPool(device,&pi,nullptr,&pool));
   VkDescriptorSetAllocateInfo si{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};si.descriptorPool=pool;si.descriptorSetCount=1;si.pSetLayouts=&descriptors;checked(xrgpu::vulkan_dispatch().vkAllocateDescriptorSets(device,&si,&set));
   VkDescriptorBufferInfo bufferInfo[3]{};VkWriteDescriptorSet writes[3]{};
   for(int i=0;i<3;i++){bufferInfo[i]={buffers[i],0,sizes[i]};writes[i]={VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};writes[i].dstSet=set;writes[i].dstBinding=i;writes[i].descriptorCount=1;writes[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;writes[i].pBufferInfo=&bufferInfo[i];}
   xrgpu::vulkan_dispatch().vkUpdateDescriptorSets(device,3,writes,0,nullptr);
   VkPushConstantRange push{VK_SHADER_STAGE_COMPUTE_BIT,0,16};VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pli.setLayoutCount=1;pli.pSetLayouts=&descriptors;pli.pushConstantRangeCount=1;pli.pPushConstantRanges=&push;checked(xrgpu::vulkan_dispatch().vkCreatePipelineLayout(device,&pli,nullptr,&layout));
   VkCommandPoolCreateInfo ci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};ci.queueFamilyIndex=family;ci.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;checked(xrgpu::vulkan_dispatch().vkCreateCommandPool(device,&ci,nullptr,&commands));
   VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=commands;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;checked(xrgpu::vulkan_dispatch().vkAllocateCommandBuffers(device,&ca,&command));
   if(detailed){VkQueryPoolCreateInfo q{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};q.queryType=VK_QUERY_TYPE_TIMESTAMP;q.queryCount=16;checked(xrgpu::vulkan_dispatch().vkCreateQueryPool(device,&q,nullptr,&queries));}
   VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};checked(xrgpu::vulkan_dispatch().vkCreateFence(device,&fi,nullptr,&fence));check();
 }
 VkPipeline pipeline(const uint32_t* code,size_t length){
   check();VkShaderModule shader{};VkShaderModuleCreateInfo s{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};s.codeSize=length;s.pCode=code;checked(xrgpu::vulkan_dispatch().vkCreateShaderModule(device,&s,nullptr,&shader));
   VkComputePipelineCreateInfo p{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};p.layout=layout;p.stage={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};p.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT;p.stage.module=shader;p.stage.pName="main";
   VkPipeline result{};auto status=xrgpu::vulkan_dispatch().vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&p,nullptr,&result);xrgpu::vulkan_dispatch().vkDestroyShaderModule(device,shader,nullptr);checked(status);pipelines.push_back(result);return result;
 }
 void sample(){
   // Allocation and input copies happen at setup. This timer covers command recording,
   // submission and the completion wait; output readback happens in residentRead.
   deadline=Clock::now()+std::chrono::seconds(15);auto start=Clock::now();
   check();checked(xrgpu::vulkan_dispatch().vkResetFences(device,1,&fence));checked(xrgpu::vulkan_dispatch().vkResetCommandBuffer(command,0));VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};checked(xrgpu::vulkan_dispatch().vkBeginCommandBuffer(command,&begin));
   if(detailed)xrgpu::vulkan_dispatch().vkCmdResetQueryPool(command,queries,0,16);
   VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT|VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT;
   xrgpu::vulkan_dispatch().vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_HOST_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&barrier,0,nullptr,0,nullptr);
   xrgpu::vulkan_dispatch().vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipelines[0]);xrgpu::vulkan_dispatch().vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,&set,0,nullptr);
   xrgpu::vulkan_dispatch().vkCmdPushConstants(command,layout,VK_SHADER_STAGE_COMPUTE_BIT,0,16,dims);
   for(uint32_t r=0;r<repeats;r++){
     if(r){VkMemoryBarrier dependency{VK_STRUCTURE_TYPE_MEMORY_BARRIER};dependency.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;dependency.dstAccessMask=VK_ACCESS_SHADER_WRITE_BIT;
       xrgpu::vulkan_dispatch().vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&dependency,0,nullptr,0,nullptr);}
     if(detailed)xrgpu::vulkan_dispatch().vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,queries,r*2);
     xrgpu::vulkan_dispatch().vkCmdDispatch(command,groups,1,1);
     if(detailed)xrgpu::vulkan_dispatch().vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,queries,r*2+1);
   }
   barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
   xrgpu::vulkan_dispatch().vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);checked(xrgpu::vulkan_dispatch().vkEndCommandBuffer(command));
   VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;checked(xrgpu::vulkan_dispatch().vkQueueSubmit(queue,1,&submit,fence));
   auto remaining=std::chrono::duration_cast<std::chrono::nanoseconds>(deadline-Clock::now()).count();
   checked(xrgpu::vulkan_dispatch().vkWaitForFences(device,1,&fence,VK_TRUE,std::max<int64_t>(1,remaining)));check();
   wall=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
   events=Json::array();gpu=0;
   if(detailed){
     uint64_t times[16]{};checked(xrgpu::vulkan_dispatch().vkGetQueryPoolResults(device,queries,0,2*repeats,sizeof(times),times,sizeof(uint64_t),VK_QUERY_RESULT_64_BIT));
     uint64_t mask=bits==64?UINT64_MAX:((uint64_t(1)<<bits)-1);double period=properties.limits.timestampPeriod;
     for(uint32_t r=0;r<repeats;r++){
       double begin=double((times[2*r]-times[0])&mask)*period,end=double((times[2*r+1]-times[0])&mask)*period;
       if(end<begin)throw std::runtime_error("Resident timestamp sequence wrapped");
       events.push_back({{"kernel_name",candidate?"resident_matmul_tile2":"resident_matmul_tile1"},{"dispatch_id",r},
         {"operator","matmul"},{"global_size",{groups*64,1,1}},{"local_size",{64,1,1}},
         {"start_ns",begin},{"end_ns",end},{"ms",(end-begin)/1e6}});
     }
     gpu=double((times[2*repeats-1]-times[0])&mask)*period/1e6;
   }
 }
 Json stats(){double sum=0;for(auto& e:events)sum+=e["ms"].get<double>();return {{"execution_scope","resident_buffers"},{"model_ms",wall},{"gpu_ms",detailed?Json(sum):Json(nullptr)},
   {"gpu_span_ms",detailed?Json(gpu):Json(nullptr)},{"gpu_span_scope",detailed?"single dispatch sequence, common queue timestamp domain":"unavailable"},
   {"dispatch_count",repeats},{"dispatches",events},{"kernel_variant",candidate?"candidate":"stock"},{"candidate_selected",candidate},
   {"validation_control",control},{"profiling",detailed?"trace":"latency"},{"output_shape",{dims[0],dims[2]}},
   {"persistent_bytes",sizes[0]+sizes[1]+sizes[2]},{"memory_device_local",deviceLocal},
   {"input_staging_ms",nullptr},{"output_staging_ms",nullptr}};}
};
Resident& resident(jlong h){if(!h)throw std::runtime_error("Closed resident runner");return *reinterpret_cast<Resident*>(h);}
void fail(JNIEnv* env,const std::exception& e){env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());}
}
extern "C" JNIEXPORT jlong JNICALL Java_org_xrgpu_benchmarks_Native_residentCreate(JNIEnv* env,jclass,jbyteArray a,jbyteArray b,jint m,jint k,jint n,jboolean candidate,jboolean detailed,jstring test){
 try{
   const char* raw=env->GetStringUTFChars(test,nullptr);std::string control(raw);env->ReleaseStringUTFChars(test,raw);
   if(control!="none"&&control!="slower"&&control!="incorrect")throw std::runtime_error("Unknown resident control");
   auto s=std::make_unique<Resident>();s->initialize(m,k,n,candidate,detailed,control);
   if(env->GetArrayLength(a)!=s->sizes[0]||env->GetArrayLength(b)!=s->sizes[1])throw std::runtime_error("Resident input size mismatch");
   env->GetByteArrayRegion(a,0,s->sizes[0],static_cast<jbyte*>(s->mapped[0]));env->GetByteArrayRegion(b,0,s->sizes[1],static_cast<jbyte*>(s->mapped[1]));
   s->pipeline(candidate?candidate_code:stock_code,candidate?sizeof(candidate_code):sizeof(stock_code));return reinterpret_cast<jlong>(s.release());
 }catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_Native_residentRun(JNIEnv* env,jclass,jlong h){try{resident(h).sample();}catch(const std::exception& e){fail(env,e);}}
extern "C" JNIEXPORT jfloatArray JNICALL Java_org_xrgpu_benchmarks_Native_residentRead(JNIEnv* env,jclass,jlong h){try{
 auto& s=resident(h);auto out=env->NewFloatArray(s.sizes[2]/4);if(!out)throw std::runtime_error("Readback allocation failed");
 env->SetFloatArrayRegion(out,0,s.sizes[2]/4,static_cast<float*>(s.mapped[2]));return out;
 }catch(const std::exception& e){fail(env,e);return nullptr;}}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_residentStats(JNIEnv* env,jclass,jlong h){try{return env->NewStringUTF(resident(h).stats().dump().c_str());}catch(const std::exception& e){fail(env,e);return nullptr;}}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_Native_residentDestroy(JNIEnv*,jclass,jlong h){delete reinterpret_cast<Resident*>(h);}
