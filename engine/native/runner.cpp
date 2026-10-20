#include "trace.h"
#include "capabilities.h"
#include "scene.h"
#include "tensor_validation.h"
#include "gpu_span.h"
#include "kernel_variant.h"
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
struct Session {
  xrgpu::Trace* trace;
  std::unique_ptr<Module> model;
  double load_ms=0,model_ms=0,timestamp_scale=1;
  Json shape=Json::array();
  bool candidate=false,selected=false,detailed=true;
  std::string control="none";
  explicit Session(const std::string& path,const std::string& variant,bool trace,const std::string& test):candidate(variant=="candidate"),detailed(trace),control(test){
    if(control!="none"&&control!="incorrect"&&control!="slower")throw std::runtime_error("Invalid kernel validation control");
    xrbench::incorrect_kernel=candidate&&control=="incorrect";
    if(variant!="stock"&&variant!="candidate")throw std::runtime_error("Unknown kernel variant");
    xrbench::candidate_kernel=candidate;xrbench::candidate_selections=0;
    executorch::runtime::runtime_init();
    auto caps=xrgpu::capabilities();
    if(!caps["backends"]["executorch_vulkan"]["compatible"].get<bool>())throw std::runtime_error("ExecuTorch Vulkan requirements unavailable");xrbench::gpu_timestamps_supported=detailed&&caps["gpu_timestamps"].get<bool>();
    // The pinned QueryPool rounds ns/tick to an integer. Correct the new span to
    // the actual Vulkan period; retain the historical gpu_ms sum unchanged.
    if(xrbench::gpu_timestamps_supported){double period=caps["timestamp_period_ns"].get<double>();
      long rounded=std::lround(period);timestamp_scale=period/(rounded?rounded:52);}
    auto t=std::make_unique<xrgpu::Trace>();trace=t.get();
    const double start=xrgpu::milliseconds();
    model=std::make_unique<Module>(path,Module::LoadMode::Mmap,std::move(t));
    if(model->load_forward()!=executorch::runtime::Error::Ok)throw std::runtime_error("Program load failed");
    load_ms=xrgpu::milliseconds()-start;
    selected=xrbench::candidate_selections>0;
    if(candidate&&!selected)throw std::runtime_error("Candidate did not select a supported matmul shader");
  }
};
Session& session(jlong handle){if(!handle)throw std::runtime_error("Closed model");return *reinterpret_cast<Session*>(handle);}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_capabilities(JNIEnv* env,jclass){
  try{return env->NewStringUTF(xrgpu::capabilities().dump().c_str());}catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jlong JNICALL Java_org_xrgpu_benchmarks_Native_create(JNIEnv* env,jclass,jstring path,jstring variant,jboolean detailed,jstring control){
  try{return reinterpret_cast<jlong>(new Session(text(env,path),text(env,variant),detailed,text(env,control)));}catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_Native_destroy(JNIEnv*,jclass,jlong handle){delete reinterpret_cast<Session*>(handle);}
extern "C" JNIEXPORT jfloatArray JNICALL Java_org_xrgpu_benchmarks_Native_infer(JNIEnv* env,jclass,jlong handle,jobjectArray data,jobjectArray dimensions,jobjectArray types){
  try{
    auto& s=session(handle);const auto count=env->GetArrayLength(data);
    xrbench::candidate_kernel=s.candidate;
    xrbench::incorrect_kernel=s.candidate&&s.control=="incorrect";
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
    s.trace->reset();const double start=xrgpu::milliseconds();
    if(s.candidate&&s.control=="slower")for(int repeat=1;repeat<8;repeat++){
      auto repeated=s.model->forward(inputs);
      if(!repeated.ok())throw std::runtime_error("Slow control execution failed");
    }
    auto outputs=s.model->forward(inputs);
    s.model_ms=xrgpu::milliseconds()-start;
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
    Json span=nullptr;
    size_t executions=0;for(auto& e:s.trace->host)if(e["name"]=="ETVK_COMPUTE_GRAPH_EXECUTE")executions++;
    // One graph owns one Context/queue and query pool in this backend.
    // Combining several graphs would assume a shared clock/queue we haven't verified.
    xrgpu::GpuSpan spanEvents;
    for(auto& e:s.trace->gpu)spanEvents.add(e["start_ns"].get<uint64_t>(),e["end_ns"].get<uint64_t>());
    if(auto value=spanEvents.finish(executions,s.trace->invalid_gpu_timestamp,s.timestamp_scale))span=*value;
    Json dispatches=Json::array();
    for(auto e:s.trace->gpu){
      e["raw_start_ns"]=e["start_ns"];e["raw_end_ns"]=e["end_ns"];
      e["start_ns"]=e["start_ns"].get<double>()*s.timestamp_scale;
      e["end_ns"]=e["end_ns"].get<double>()*s.timestamp_scale;
      e["ms"]=e["ms"].get<double>()*s.timestamp_scale;
      dispatches.push_back(e);
    }
    Json stats={{"model_ms",s.model_ms},{"load_ms",s.load_ms},{"gpu_ms",gpu},{"gpu_span_ms",span},
      {"dispatches",dispatches},{"timestamp_scale",s.timestamp_scale},
      {"validation_control",s.control},{"kernel_variant",s.candidate?"candidate":"stock"},{"candidate_selected",s.selected},
      {"profiling",s.detailed?"trace":"latency"},
      {"gpu_span_scope",span.is_null()?"unavailable":"single graph, common queue timestamp domain"},
      {"dispatch_count",s.trace->gpu.empty()?Json(nullptr):Json(s.trace->gpu.size())},{"input_staging_ms",input},
      {"output_staging_ms",output},{"submit_and_wait_ms",wait},{"output_shape",s.shape}};
    return env->NewStringUTF(stats.dump().c_str());
  }catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_Native_scene(JNIEnv* env,jclass,jboolean reset,jboolean frames){
  return env->NewStringUTF(xrgpu::scene_snapshot(reset,frames).dump().c_str());
}
