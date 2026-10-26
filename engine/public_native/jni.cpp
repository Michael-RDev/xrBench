#include "benchmark.h"
#include <jni.h>
static std::string text(JNIEnv* env,jstring value){const char* data=env->GetStringUTFChars(value,nullptr);std::string result=data;env->ReleaseStringUTFChars(value,data);return result;}
extern "C" JNIEXPORT jstring JNICALL Java_org_xrgpu_benchmarks_NativeOperators_probe(JNIEnv* env,jclass){
    try{return env->NewStringUTF(xrbench::probe().dump().c_str());}
    catch(const std::exception& e){env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_org_xrgpu_benchmarks_NativeOperators_run(JNIEnv* env,jclass,jstring root,jstring config,jstring output,jstring device){
    try{xrbench::benchmark(text(env,root),nlohmann::json::parse(text(env,config)),text(env,output),nlohmann::json::parse(text(env,device)));}
    catch(const std::exception& e){env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());}
}
