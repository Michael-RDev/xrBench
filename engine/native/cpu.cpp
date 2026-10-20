#include "tensor_validation.h"
#include "kernels/cpu/matmul.h"
#include <executorch/extension/module/module.h>
#include <executorch/extension/tensor/tensor.h>
#include <executorch/runtime/platform/runtime.h>
#include <nlohmann/json.hpp>
#include <jni.h>
#include <chrono>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using Json = nlohmann::json;
using executorch::extension::Module;
using executorch::extension::make_tensor_ptr;
using executorch::runtime::EValue;
namespace {
double now(){return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();}
std::string text(JNIEnv* env,jstring value){
  if(!value)throw std::runtime_error("Missing string");
  const char* chars=env->GetStringUTFChars(value,nullptr);
  if(!chars)throw std::runtime_error("String allocation failed");
  std::string result(chars);env->ReleaseStringUTFChars(value,chars);return result;
}
void fail(JNIEnv* env,const std::exception& e){env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());}
struct Session {
  std::unique_ptr<Module> model;
  bool resident=false,candidate=false;std::string control="none";
  int m=0,k=0,n=0;std::vector<float> a,b,c;Json shape=Json::array();
  double model_ms=0,load_ms=0;
};
Session& session(jlong handle){if(!handle)throw std::runtime_error("Closed CPU session");return *reinterpret_cast<Session*>(handle);}
jfloatArray array(JNIEnv* env,const float* data,size_t size){
  auto result=env->NewFloatArray(size);if(!result)throw std::runtime_error("Output allocation failed");
  env->SetFloatArrayRegion(result,0,size,data);return result;
}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_NativeCPU_capabilities(JNIEnv* env,jclass){
  Json caps={{"runtime_revision",XRGPU_ET_REVISION},{"runner_revision",XRGPU_RUNNER_REVISION},
    {"cpu",{{"abi","arm64-v8a"},{"threading_policy","single calling thread"},{"neon",true}}},
    {"backends",{{"executorch_cpu",{{"compatible",true},{"status","compatible_unvalidated"},{"reason","Portable native CPU kernels; correctness checked per workload"}}},
                  {"cpu_resident",{{"compatible",true},{"status","compatible_unvalidated"},{"reason","ARM64 FP32 matmul, scalar / NEON; maximum 64 MiB persistent buffers"}}}}}};
  return env->NewStringUTF(caps.dump().c_str());
}
extern "C" JNIEXPORT jlong JNICALL Java_org_xrgpu_benchmarks_NativeCPU_create(JNIEnv* env,jclass,jstring path){
  try{
    auto s=std::make_unique<Session>();executorch::runtime::runtime_init();double start=now();
    s->model=std::make_unique<Module>(text(env,path),Module::LoadMode::Mmap);
    if(s->model->load_forward()!=executorch::runtime::Error::Ok)throw std::runtime_error("CPU program load failed");
    s->load_ms=now()-start;return reinterpret_cast<jlong>(s.release());
  }catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT jlong JNICALL Java_org_xrgpu_benchmarks_NativeCPU_residentCreate(JNIEnv* env,jclass,jbyteArray a,jbyteArray b,jint m,jint k,jint n,jboolean candidate,jstring control){
  try{
    if(m<=0||k<=0||n<=0)throw std::runtime_error("Invalid matmul dimensions");
    auto s=std::make_unique<Session>();s->resident=true;s->candidate=candidate;s->control=text(env,control);
    if(s->control!="none"&&s->control!="incorrect"&&s->control!="slower")throw std::runtime_error("Invalid control");
    const uint64_t ac=uint64_t(m)*k,bc=uint64_t(k)*n,cc=uint64_t(m)*n;
    if(ac+bc+cc>64*1024*1024/sizeof(float)||env->GetArrayLength(a)!=ac*4||env->GetArrayLength(b)!=bc*4)
      throw std::runtime_error("CPU matmul requires matching FP32 buffers within 64 MiB");
    s->m=m;s->k=k;s->n=n;s->a.resize(ac);s->b.resize(bc);s->c.resize(cc);s->shape={m,n};
    env->GetByteArrayRegion(a,0,ac*4,reinterpret_cast<jbyte*>(s->a.data()));
    env->GetByteArrayRegion(b,0,bc*4,reinterpret_cast<jbyte*>(s->b.data()));
    return reinterpret_cast<jlong>(s.release());
  }catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_NativeCPU_residentRun(JNIEnv* env,jclass,jlong handle){
  try{auto& s=session(handle);if(!s.resident)throw std::runtime_error("Expected resident session");double start=now();
    const int repeats=s.candidate&&s.control=="slower"?8:1;
    for(int i=0;i<repeats;i++)xrbench_cpu::matmul(s.a.data(),s.b.data(),s.c.data(),s.m,s.k,s.n,s.candidate);
    if(s.candidate&&s.control=="incorrect")for(auto& v:s.c)v+=100.f;
    s.model_ms=now()-start;
  }catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT jfloatArray JNICALL Java_org_xrgpu_benchmarks_NativeCPU_residentRead(JNIEnv* env,jclass,jlong handle){
  try{auto& s=session(handle);return array(env,s.c.data(),s.c.size());}catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jfloatArray JNICALL Java_org_xrgpu_benchmarks_NativeCPU_infer(JNIEnv* env,jclass,jlong handle,jobjectArray data,jobjectArray dimensions,jobjectArray types){
  try{
    auto& s=session(handle);const int count=env->GetArrayLength(data);
    if(count<1||count>8||env->GetArrayLength(dimensions)!=count||env->GetArrayLength(types)!=count)throw std::runtime_error("Invalid CPU input count");
    std::vector<std::vector<float>> floats(count);std::vector<std::vector<int64_t>> integers(count);
    std::vector<executorch::extension::TensorPtr> tensors;std::vector<EValue> inputs;
    tensors.reserve(count);inputs.reserve(count);
    for(int i=0;i<count;i++){
      auto dims=static_cast<jintArray>(env->GetObjectArrayElement(dimensions,i));
      auto bytes=static_cast<jbyteArray>(env->GetObjectArrayElement(data,i));
      auto type=static_cast<jstring>(env->GetObjectArrayElement(types,i));
      int rank=env->GetArrayLength(dims);if(rank<1||rank>8)throw std::runtime_error("Invalid CPU input rank");
      std::vector<int32_t> shape(rank);env->GetIntArrayRegion(dims,0,rank,reinterpret_cast<jint*>(shape.data()));
      auto dtype=text(env,type);auto elements=xrgpu_benchmarks::validate_tensor(shape,dtype,env->GetArrayLength(bytes));
      if(dtype=="int64"){
        integers[i].resize(elements);env->GetByteArrayRegion(bytes,0,elements*8,reinterpret_cast<jbyte*>(integers[i].data()));
        tensors.push_back(make_tensor_ptr(shape,integers[i].data(),executorch::aten::ScalarType::Long));
      }else{
        floats[i].resize(elements);env->GetByteArrayRegion(bytes,0,elements*4,reinterpret_cast<jbyte*>(floats[i].data()));
        tensors.push_back(make_tensor_ptr(shape,floats[i].data(),executorch::aten::ScalarType::Float));
      }
      inputs.emplace_back(*tensors.back());env->DeleteLocalRef(dims);env->DeleteLocalRef(bytes);env->DeleteLocalRef(type);
    }
    double start=now();auto outputs=s.model->forward(inputs);s.model_ms=now()-start;
    if(!outputs.ok()||outputs->size()!=1||!outputs->at(0).isTensor())throw std::runtime_error("Invalid CPU output");
    auto tensor=outputs->at(0).toTensor();if(tensor.scalar_type()!=executorch::aten::ScalarType::Float)throw std::runtime_error("Expected FP32 CPU output");
    s.shape=Json::array();for(auto d:tensor.sizes())s.shape.push_back(d);
    return array(env,tensor.const_data_ptr<float>(),tensor.numel());
  }catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_NativeCPU_stats(JNIEnv* env,jclass,jlong handle){
  try{auto& s=session(handle);Json stats={{"processor","cpu"},{"model_ms",s.model_ms},{"load_ms",s.load_ms},{"output_shape",s.shape},
      {"gpu_ms",nullptr},{"gpu_span_ms",nullptr},{"dispatch_count",nullptr},{"dispatches",Json::array()},
      {"kernel_variant",s.candidate?"candidate":"stock"},{"candidate_selected",s.candidate},{"validation_control",s.control},
      {"kernel_source",s.resident?"kernels/cpu/matmul.h":"ExecuTorch portable CPU kernels"},
      {"kernel_name",s.resident?(s.candidate?"cpu_matmul_neon":"cpu_matmul_scalar"):"executorch_portable"},
      {"threading_policy","single calling thread"},{"threads",1}};
    return env->NewStringUTF(stats.dump().c_str());
  }catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_NativeCPU_destroy(JNIEnv*,jclass,jlong handle){delete reinterpret_cast<Session*>(handle);}
