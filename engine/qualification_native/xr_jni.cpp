#include "runner.h"
#include "../native/scene.h"
#include <jni.h>
#include <atomic>
#include <chrono>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <thread>

namespace {
std::atomic<bool> cancelled{false};
using Json = nlohmann::json;
Json scene(JNIEnv* env,jclass activity,bool reset,bool frames) {
  if(cancelled.load())throw std::runtime_error("XR qualification activity paused");
  auto state=xrgpu::scene_snapshot(reset,frames);
  if(!state.value("error","").empty())throw std::runtime_error(state.at("error").get<std::string>());
  auto method=env->GetStaticMethodID(activity,"conditions0","()Ljava/lang/String;");
  if(!method)throw std::runtime_error("Missing Android condition probe");
  auto value=static_cast<jstring>(env->CallStaticObjectMethod(activity,method));
  if(env->ExceptionCheck()) {env->ExceptionClear();throw std::runtime_error("Android condition probe unavailable");}
  if(!value)throw std::runtime_error("Android condition probe returned no data");
  const char* encoded=env->GetStringUTFChars(value,nullptr);
  if(!encoded)throw std::runtime_error("Cannot read Android conditions");
  std::string bytes(encoded);env->ReleaseStringUTFChars(value,encoded);env->DeleteLocalRef(value);
  state["conditions"]=Json::parse(bytes);
  return state;
}
}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrbench_qualification_QualificationActivity_run0(
    JNIEnv* env,jclass activity,jstring text) {
  try {
    const char* chars=env->GetStringUTFChars(text,nullptr);
    if(!chars)throw std::runtime_error("Cannot read qualification job");
    std::string encoded(chars);env->ReleaseStringUTFChars(text,chars);
    std::ifstream input(encoded);if(!input)throw std::runtime_error("Missing qualification job");
    auto job=Json::parse(input);
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(60);
    for(;;) {
      auto state=scene(env,activity,false,false);
      if(state.value("ready",false)&&state.value("focused",false)&&
         std::abs(state.value("refresh_hz",0.)-72.)<.1)break;
      if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("Focused 72 Hz OpenXR session required");
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    xrbench_qualification::execute_file(encoded,[&](bool reset,bool frames){return scene(env,activity,reset,frames);});
    std::ifstream output(job.at("output").get<std::string>());
    if(!output)throw std::runtime_error("Native qualification did not preserve evidence");
    auto result=Json::parse(output);
    return env->NewStringUTF(result.dump().c_str());
  } catch(const std::exception& error) {
    env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),error.what());return nullptr;
  }
}
extern "C" JNIEXPORT void JNICALL Java_org_xrbench_qualification_QualificationActivity_begin0(JNIEnv*,jclass) {
  cancelled.store(false);
}
extern "C" JNIEXPORT void JNICALL Java_org_xrbench_qualification_QualificationActivity_cancel0(JNIEnv*,jclass) {
  cancelled.store(true);
}
