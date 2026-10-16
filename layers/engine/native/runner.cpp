#include "trace.h"
#include "scene.h"
#include "tensor_validation.h"
#include <executorch/extension/module/module.h>
#include <executorch/extension/tensor/tensor.h>
#include <executorch/runtime/platform/runtime.h>
#include <jni.h>
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>

using Json = nlohmann::json;
using executorch::extension::Module;
using executorch::extension::make_tensor_ptr;
using executorch::runtime::EValue;
// Same inline timestamp switch used by the repository's Vulkan trace patch.
// Candidate dispatch remains at its untouched stock defaults in the backend.
#include "timing.h"
namespace {
std::string text(JNIEnv* env,jstring value) {
  if(!value) throw std::runtime_error("Missing string");
  const char* chars=env->GetStringUTFChars(value,nullptr);
  if(!chars) throw std::runtime_error("String allocation failed");
  std::string s(chars);env->ReleaseStringUTFChars(value,chars);return s;
}
void fail(JNIEnv* env,const std::exception& error) {
  env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),error.what());
}
Json capabilities() {
  Json out={{"vulkan",false},{"gpu_timestamps",false},{"runtime_revision",XRGPU_ET_REVISION},
            {"runner_revision",XRGPU_RUNNER_REVISION},{"precision","fp32"},{"variant","stock"}};
  void* lib=dlopen("libvulkan.so",RTLD_NOW|RTLD_LOCAL);
  if(!lib) return out;
  auto proc=reinterpret_cast<PFN_vkGetInstanceProcAddr>(dlsym(lib,"vkGetInstanceProcAddr"));
  if(!proc){dlclose(lib);return out;}
  auto create=reinterpret_cast<PFN_vkCreateInstance>(proc(VK_NULL_HANDLE,"vkCreateInstance"));
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
  VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;
  VkInstance instance=VK_NULL_HANDLE;
  if(create(&info,nullptr,&instance)!=VK_SUCCESS){dlclose(lib);return out;}
  auto enumerate=reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(proc(instance,"vkEnumeratePhysicalDevices"));
  auto props=reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(proc(instance,"vkGetPhysicalDeviceProperties"));
  auto queues=reinterpret_cast<PFN_vkGetPhysicalDeviceQueueFamilyProperties>(proc(instance,"vkGetPhysicalDeviceQueueFamilyProperties"));
  auto destroy=reinterpret_cast<PFN_vkDestroyInstance>(proc(instance,"vkDestroyInstance"));
  uint32_t n=0;enumerate(instance,&n,nullptr);std::vector<VkPhysicalDevice> devices(n);enumerate(instance,&n,devices.data());
  if(n){
    VkPhysicalDeviceProperties p{};props(devices[0],&p);
    uint32_t nq=0;queues(devices[0],&nq,nullptr);std::vector<VkQueueFamilyProperties> q(nq);queues(devices[0],&nq,q.data());
    bool timestamps=false;for(auto& family:q)if(family.queueFlags&VK_QUEUE_COMPUTE_BIT){timestamps=family.timestampValidBits>0;break;}
    out.update({{"vulkan",true},{"gpu",p.deviceName},{"vendor_id",p.vendorID},{"device_id",p.deviceID},
      {"driver",p.driverVersion},{"api_version",p.apiVersion},{"gpu_timestamps",timestamps&&p.limits.timestampPeriod>0},
      {"timestamp_period_ns",p.limits.timestampPeriod},{"max_workgroup_invocations",p.limits.maxComputeWorkGroupInvocations}});
  }
  destroy(instance,nullptr);dlclose(lib);return out;
}
struct Session {
  xrgpu::Trace* trace;
  std::unique_ptr<Module> model;
  double load_ms=0,model_ms=0;
  Json shape=Json::array();
  explicit Session(const std::string& path){
    executorch::runtime::runtime_init();
    xrbench::gpu_timestamps_supported=capabilities()["gpu_timestamps"].get<bool>();
    auto t=std::make_unique<xrgpu::Trace>();trace=t.get();
    const double start=xrgpu::milliseconds();
    model=std::make_unique<Module>(path,Module::LoadMode::Mmap,std::move(t));
    if(model->load_forward()!=executorch::runtime::Error::Ok)throw std::runtime_error("Program load failed");
    load_ms=xrgpu::milliseconds()-start;
  }
};
Session& session(jlong handle){if(!handle)throw std::runtime_error("Closed model");return *reinterpret_cast<Session*>(handle);}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_capabilities(JNIEnv* env,jclass){
  try{return env->NewStringUTF(capabilities().dump().c_str());}catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jlong JNICALL Java_org_xrgpu_benchmarks_Native_create(JNIEnv* env,jclass,jstring path){
  try{return reinterpret_cast<jlong>(new Session(text(env,path)));}catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_Native_destroy(JNIEnv*,jclass,jlong handle){delete reinterpret_cast<Session*>(handle);}
extern "C" JNIEXPORT jfloatArray JNICALL Java_org_xrgpu_benchmarks_Native_infer(JNIEnv* env,jclass,jlong handle,jobjectArray data,jobjectArray dimensions,jobjectArray types){
  try{
    auto& s=session(handle);const auto count=env->GetArrayLength(data);
    if(count<1||count>8||env->GetArrayLength(dimensions)!=count||env->GetArrayLength(types)!=count)throw std::runtime_error("Invalid input count");
    std::vector<std::vector<int64_t>> integers(count);
    std::vector<std::vector<float>> floats(count);
    std::vector<executorch::extension::TensorPtr> tensors;
    std::vector<EValue> inputs;tensors.reserve(count);inputs.reserve(count);
    for(int i=0;i<count;++i){
      auto shape=static_cast<jintArray>(env->GetObjectArrayElement(dimensions,i));
      auto bytes=static_cast<jbyteArray>(env->GetObjectArrayElement(data,i));
      auto type=static_cast<jstring>(env->GetObjectArrayElement(types,i));
      const int rank=env->GetArrayLength(shape);if(rank<1||rank>8)throw std::runtime_error("Invalid rank");
      std::vector<int32_t> sizes(rank);env->GetIntArrayRegion(shape,0,rank,reinterpret_cast<jint*>(sizes.data()));
      const auto dtype=text(env,type);const bool integral=dtype=="int64";
      const auto elements=xrgpu_benchmarks::validate_tensor(sizes,dtype,env->GetArrayLength(bytes));
      if(integral){integers[i].resize(elements);env->GetByteArrayRegion(bytes,0,elements*8,reinterpret_cast<jbyte*>(integers[i].data()));
        tensors.push_back(make_tensor_ptr(sizes,integers[i].data(),executorch::aten::ScalarType::Long));}
      else{floats[i].resize(elements);env->GetByteArrayRegion(bytes,0,elements*4,reinterpret_cast<jbyte*>(floats[i].data()));
        tensors.push_back(make_tensor_ptr(sizes,floats[i].data(),executorch::aten::ScalarType::Float));}
      inputs.emplace_back(*tensors.back());
      env->DeleteLocalRef(shape);env->DeleteLocalRef(bytes);env->DeleteLocalRef(type);
    }
    s.trace->reset();const double start=xrgpu::milliseconds();auto outputs=s.model->forward(inputs);s.model_ms=xrgpu::milliseconds()-start;
    if(!outputs.ok()||outputs->size()!=1||!outputs->at(0).isTensor())throw std::runtime_error("Invalid native output");
    const auto tensor=outputs->at(0).toTensor();
    if(tensor.scalar_type()!=executorch::aten::ScalarType::Float)throw std::runtime_error("Expected FP32 output");
    if(!s.trace->cpu_delegate.empty())throw std::runtime_error("Unexpected CPU delegate execution");
    s.shape=Json::array();for(auto dim:tensor.sizes())s.shape.push_back(dim);
    auto result=env->NewFloatArray(tensor.numel());if(!result)throw std::runtime_error("Output allocation failed");
    env->SetFloatArrayRegion(result,0,tensor.numel(),tensor.const_data_ptr<float>());return result;
  }catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_stats(JNIEnv* env,jclass,jlong handle){
  try{
    auto& s=session(handle);Json gpu=nullptr,input=nullptr,output=nullptr,wait=nullptr;
    for(auto& e:s.trace->gpu)gpu=(gpu.is_null()?0.:gpu.get<double>())+e["ms"].get<double>();
    for(auto& e:s.trace->host){auto name=e["name"].get<std::string>();Json* value=name=="ETVK_COPY_INPUTS"?&input:name=="ETVK_COPY_OUTPUTS"?&output:name=="ETVK_COMPUTE_GRAPH_EXECUTE"?&wait:nullptr;
      if(value)*value=(value->is_null()?0.:value->get<double>())+e["ms"].get<double>();}
    Json stats={{"model_ms",s.model_ms},{"load_ms",s.load_ms},{"gpu_ms",gpu},{"input_staging_ms",input},
      {"output_staging_ms",output},{"submit_and_wait_ms",wait},{"output_shape",s.shape}};
    return env->NewStringUTF(stats.dump().c_str());
  }catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_scene(JNIEnv* env,jclass,jboolean reset,jboolean frames){
  return env->NewStringUTF(xrgpu::scene_snapshot(reset,frames).dump().c_str());
}
