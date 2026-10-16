#include "scene.h"
#include <android_native_app_glue.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace xrgpu {
namespace {
std::mutex telemetry_mutex;
bool focused = false, ready = false;
float refresh = 0;
std::string error;
std::vector<double> frame_ms;
uint64_t rendered = 0;
int64_t dropped = -1, dropped_start = -1;
bool invalid_metrics = false;
void xrcheck(XrResult result, const char* action) {
  if (XR_FAILED(result)) throw std::runtime_error(std::string(action) + ": " + std::to_string(result));
}
template<class T> T xrfunction(XrInstance instance, const char* name) {
  PFN_xrVoidFunction function = nullptr;
  xrcheck(xrGetInstanceProcAddr(instance, name, &function), name);
  return reinterpret_cast<T>(function);
}
GLuint shader(GLenum type, const char* source) {
  GLuint s = glCreateShader(type); glShaderSource(s, 1, &source, nullptr); glCompileShader(s);
  GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
  if (!ok) { char message[1024]; glGetShaderInfoLog(s, sizeof(message), nullptr, message); throw std::runtime_error(message); }
  return s;
}
using Matrix = std::array<float, 16>;
Matrix multiply(const Matrix& a, const Matrix& b) {
  Matrix result{};
  for (int c=0; c<4; ++c) for (int r=0; r<4; ++r) for (int k=0; k<4; ++k)
    result[c*4+r] += a[k*4+r]*b[c*4+k];
  return result;
}
Matrix view_projection(const XrView& view) {
  const auto& q=view.pose.orientation; const auto& t=view.pose.position;
  // Transpose the quaternion rotation to invert the rigid camera transform.
  Matrix inverse{1-2*q.y*q.y-2*q.z*q.z,2*q.x*q.y-2*q.z*q.w,2*q.x*q.z+2*q.y*q.w,0,
    2*q.x*q.y+2*q.z*q.w,1-2*q.x*q.x-2*q.z*q.z,2*q.y*q.z-2*q.x*q.w,0,
    2*q.x*q.z-2*q.y*q.w,2*q.y*q.z+2*q.x*q.w,1-2*q.x*q.x-2*q.y*q.y,0,0,0,0,1};
  for(int row=0;row<3;++row) inverse[12+row]=-(inverse[row]*t.x+inverse[4+row]*t.y+inverse[8+row]*t.z);
  float l=std::tan(view.fov.angleLeft),r=std::tan(view.fov.angleRight),
        b=std::tan(view.fov.angleDown),u=std::tan(view.fov.angleUp), near=.05f, far=100.f;
  Matrix p{}; p[0]=2/(r-l); p[5]=2/(u-b); p[8]=(r+l)/(r-l); p[9]=(u+b)/(u-b);
  p[10]=-(far+near)/(far-near); p[11]=-1; p[14]=-2*far*near/(far-near);
  return multiply(p,inverse);
}

struct Eye {
  XrSwapchain swapchain=XR_NULL_HANDLE;
  std::vector<XrSwapchainImageOpenGLESKHR> images;
  GLuint depth=0;
  uint32_t width=0,height=0;
};
class Scene {
  XrInstance instance=XR_NULL_HANDLE;
  XrSession session=XR_NULL_HANDLE;
  XrSpace space=XR_NULL_HANDLE;
  EGLDisplay display=EGL_NO_DISPLAY;
  EGLContext context=EGL_NO_CONTEXT;
  EGLSurface surface=EGL_NO_SURFACE;
  GLuint program=0, vao=0, vbo=0, fbo=0;
  GLint matrix_location=-1;
  std::vector<Eye> eyes;
  bool running=false, metrics=false;
  PFN_xrGetDisplayRefreshRateFB get_refresh=nullptr;
  PFN_xrQueryPerformanceMetricsCounterMETA query=nullptr;
  XrPath cpu_path=XR_NULL_PATH,gpu_path=XR_NULL_PATH,drop_path=XR_NULL_PATH;
 public:
  ~Scene() {
    { std::lock_guard<std::mutex> lock(telemetry_mutex); ready=false; focused=false; }
    if(display!=EGL_NO_DISPLAY && context!=EGL_NO_CONTEXT) {
      for(auto& e:eyes) if(e.depth) glDeleteRenderbuffers(1,&e.depth);
      glDeleteFramebuffers(1,&fbo); glDeleteBuffers(1,&vbo); glDeleteVertexArrays(1,&vao);
      if(program) glDeleteProgram(program);
    }
    for(auto& e:eyes) if(e.swapchain) xrDestroySwapchain(e.swapchain);
    if(space) xrDestroySpace(space);
    if(session) xrDestroySession(session);
    if(instance) xrDestroyInstance(instance);
    if(display!=EGL_NO_DISPLAY) {
      eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
      if(context!=EGL_NO_CONTEXT) eglDestroyContext(display,context);
      if(surface!=EGL_NO_SURFACE) eglDestroySurface(display,surface);
      eglTerminate(display);
    }
  }
  void init(android_app* app) {
    { std::lock_guard<std::mutex> lock(telemetry_mutex); error.clear(); ready=false; focused=false; frame_ms.clear(); }
    auto initialize=xrfunction<PFN_xrInitializeLoaderKHR>(XR_NULL_HANDLE,"xrInitializeLoaderKHR");
    XrLoaderInitInfoAndroidKHR loader{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
    loader.applicationVM=app->activity->vm; loader.applicationContext=app->activity->clazz;
    xrcheck(initialize(reinterpret_cast<XrLoaderInitInfoBaseHeaderKHR*>(&loader)),"initialize loader");
    uint32_t count=0; xrcheck(xrEnumerateInstanceExtensionProperties(nullptr,0,&count,nullptr),"extensions");
    std::vector<XrExtensionProperties> properties(count,{XR_TYPE_EXTENSION_PROPERTIES});
    xrcheck(xrEnumerateInstanceExtensionProperties(nullptr,count,&count,properties.data()),"extensions");
    auto available=[&](const char* name) { return std::any_of(properties.begin(),properties.end(),[&](auto& p){return std::strcmp(p.extensionName,name)==0;}); };
    std::vector<const char*> extensions={XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME,XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME};
    bool rates=available(XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
    metrics=available(XR_META_PERFORMANCE_METRICS_EXTENSION_NAME);
    if(rates) extensions.push_back(XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
    if(metrics) extensions.push_back(XR_META_PERFORMANCE_METRICS_EXTENSION_NAME);
    XrInstanceCreateInfoAndroidKHR android{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    android.applicationVM=app->activity->vm; android.applicationActivity=app->activity->clazz;
    XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO}; create.next=&android;
    std::strcpy(create.applicationInfo.applicationName,"xrGPU"); create.applicationInfo.apiVersion=XR_API_VERSION_1_0;
    create.enabledExtensionCount=extensions.size(); create.enabledExtensionNames=extensions.data();
    xrcheck(xrCreateInstance(&create,&instance),"create XR instance");
    XrSystemGetInfo sysinfo{XR_TYPE_SYSTEM_GET_INFO}; sysinfo.formFactor=XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system; xrcheck(xrGetSystem(instance,&sysinfo,&system),"get XR system");
    auto requirements=xrfunction<PFN_xrGetOpenGLESGraphicsRequirementsKHR>(instance,"xrGetOpenGLESGraphicsRequirementsKHR");
    XrGraphicsRequirementsOpenGLESKHR req{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
    xrcheck(requirements(instance,system,&req),"graphics requirements");
    display=eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if(!eglInitialize(display,nullptr,nullptr)) throw std::runtime_error("EGL initialization failed");
    EGLint attributes[]={EGL_RENDERABLE_TYPE,EGL_OPENGL_ES3_BIT,EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,
      EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_NONE};
    EGLConfig config; EGLint configs;
    if(!eglChooseConfig(display,attributes,&config,1,&configs)||!configs) throw std::runtime_error("No GLES3 EGL config");
    EGLint ca[]={EGL_CONTEXT_CLIENT_VERSION,3,EGL_NONE}; context=eglCreateContext(display,config,EGL_NO_CONTEXT,ca);
    EGLint pa[]={EGL_WIDTH,16,EGL_HEIGHT,16,EGL_NONE}; surface=eglCreatePbufferSurface(display,config,pa);
    if(context==EGL_NO_CONTEXT||surface==EGL_NO_SURFACE||!eglMakeCurrent(display,surface,surface,context)) throw std::runtime_error("EGL context failed");
    XrGraphicsBindingOpenGLESAndroidKHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
    binding.display=display;binding.config=config;binding.context=context;
    XrSessionCreateInfo sci{XR_TYPE_SESSION_CREATE_INFO}; sci.next=&binding;sci.systemId=system;
    xrcheck(xrCreateSession(instance,&sci,&session),"create session");
    if(rates) {
      auto request=xrfunction<PFN_xrRequestDisplayRefreshRateFB>(instance,"xrRequestDisplayRefreshRateFB");
      get_refresh=xrfunction<PFN_xrGetDisplayRefreshRateFB>(instance,"xrGetDisplayRefreshRateFB");
      xrcheck(request(session,72.f),"request 72 Hz");
    }
    XrReferenceSpaceCreateInfo ref{XR_TYPE_REFERENCE_SPACE_CREATE_INFO}; ref.referenceSpaceType=XR_REFERENCE_SPACE_TYPE_LOCAL; ref.poseInReferenceSpace.orientation.w=1;
    xrcheck(xrCreateReferenceSpace(session,&ref,&space),"reference space");
    xrcheck(xrEnumerateViewConfigurationViews(instance,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,0,&count,nullptr),"views");
    std::vector<XrViewConfigurationView> views(count,{XR_TYPE_VIEW_CONFIGURATION_VIEW});
    xrcheck(xrEnumerateViewConfigurationViews(instance,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,count,&count,views.data()),"views");
    if(count!=2) throw std::runtime_error("Expected stereo views");
    uint32_t nf=0; xrcheck(xrEnumerateSwapchainFormats(session,0,&nf,nullptr),"formats");
    std::vector<int64_t> formats(nf); xrcheck(xrEnumerateSwapchainFormats(session,nf,&nf,formats.data()),"formats");
    int64_t format=std::find(formats.begin(),formats.end(),GL_SRGB8_ALPHA8)!=formats.end()?GL_SRGB8_ALPHA8:GL_RGBA8;
    if(std::find(formats.begin(),formats.end(),format)==formats.end()) throw std::runtime_error("No supported RGBA swapchain");
    eyes.resize(count);
    for(uint32_t i=0;i<count;++i) {
      auto& e=eyes[i]; e.width=1440;e.height=1440;
      if(views[i].maxImageRectWidth<e.width||views[i].maxImageRectHeight<e.height) throw std::runtime_error("Fixed scene resolution unsupported");
      XrSwapchainCreateInfo sc{XR_TYPE_SWAPCHAIN_CREATE_INFO};sc.usageFlags=XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
      sc.format=format;sc.sampleCount=1;sc.width=e.width;sc.height=e.height;sc.faceCount=1;sc.arraySize=1;sc.mipCount=1;
      xrcheck(xrCreateSwapchain(session,&sc,&e.swapchain),"swapchain");
      uint32_t ni;xrcheck(xrEnumerateSwapchainImages(e.swapchain,0,&ni,nullptr),"images");
      e.images.resize(ni,{XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
      xrcheck(xrEnumerateSwapchainImages(e.swapchain,ni,&ni,reinterpret_cast<XrSwapchainImageBaseHeader*>(e.images.data())),"images");
      glGenRenderbuffers(1,&e.depth);glBindRenderbuffer(GL_RENDERBUFFER,e.depth);glRenderbufferStorage(GL_RENDERBUFFER,GL_DEPTH_COMPONENT24,e.width,e.height);
    }
    GLuint vs=shader(GL_VERTEX_SHADER,R"(#version 300 es
      layout(location=0) in vec3 position; uniform mat4 vp; out vec3 color;
      void main(){int i=gl_InstanceID;vec3 offset=vec3(float(i%8)-3.5,float(i/8)-3.5,-10.0)*0.3;
      gl_Position=vp*vec4(position*0.11+offset,1);color=vec3(0.2+float(i%3)*0.2,0.5,0.8);})");
    GLuint fs=shader(GL_FRAGMENT_SHADER,R"(#version 300 es
      precision mediump float; in vec3 color; out vec4 outputColor;
      void main(){outputColor=vec4(color,1);})");
    program=glCreateProgram();glAttachShader(program,vs);glAttachShader(program,fs);glLinkProgram(program);
    GLint linked;glGetProgramiv(program,GL_LINK_STATUS,&linked);glDeleteShader(vs);glDeleteShader(fs);
    if(!linked) throw std::runtime_error("Scene shader link failed");
    matrix_location=glGetUniformLocation(program,"vp");
    std::vector<float> vertices;
    const int indices[]={0,1,2,2,3,0};
    const float corners[8][3]={{-1,-1,-1},{1,-1,-1},{1,1,-1},{-1,1,-1},{-1,-1,1},{1,-1,1},{1,1,1},{-1,1,1}};
    const int faces[6][4]={{0,1,2,3},{5,4,7,6},{4,0,3,7},{1,5,6,2},{3,2,6,7},{4,5,1,0}};
    for(auto& face:faces) for(int index:indices) for(float value:corners[face[index]]) vertices.push_back(value);
    glGenVertexArrays(1,&vao);glBindVertexArray(vao);glGenBuffers(1,&vbo);glBindBuffer(GL_ARRAY_BUFFER,vbo);
    glBufferData(GL_ARRAY_BUFFER,vertices.size()*sizeof(float),vertices.data(),GL_STATIC_DRAW);
    glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,0,nullptr);glEnableVertexAttribArray(0);glGenFramebuffers(1,&fbo);
    if(metrics) {
      auto enumerate=xrfunction<PFN_xrEnumeratePerformanceMetricsCounterPathsMETA>(instance,"xrEnumeratePerformanceMetricsCounterPathsMETA");
      auto enable=xrfunction<PFN_xrSetPerformanceMetricsStateMETA>(instance,"xrSetPerformanceMetricsStateMETA");
      query=xrfunction<PFN_xrQueryPerformanceMetricsCounterMETA>(instance,"xrQueryPerformanceMetricsCounterMETA");
      XrPerformanceMetricsStateMETA state{XR_TYPE_PERFORMANCE_METRICS_STATE_META};state.enabled=XR_TRUE;
      if(XR_FAILED(enable(session,&state))) metrics=false;
      uint32_t np=0;enumerate(instance,0,&np,nullptr);std::vector<XrPath> paths(np);enumerate(instance,np,&np,paths.data());
      for(auto path:paths) {
        char name[XR_MAX_PATH_LENGTH];uint32_t size;
        if(XR_FAILED(xrPathToString(instance,path,sizeof(name),&size,name))) continue;
        if(std::strcmp(name,"/perfmetrics_meta/app/cpu_frametime")==0) cpu_path=path;
        if(std::strcmp(name,"/perfmetrics_meta/app/gpu_frametime")==0) gpu_path=path;
        if(std::strcmp(name,"/perfmetrics_meta/compositor/dropped_frame_count")==0) drop_path=path;
      }
    }
    std::lock_guard<std::mutex> lock(telemetry_mutex); ready=true;
  }
  bool events() {
    XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
    while(xrPollEvent(instance,&event)==XR_SUCCESS) {
      if(event.type==XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING) return false;
      if(event.type==XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
        auto state=reinterpret_cast<XrEventDataSessionStateChanged*>(&event)->state;
        {std::lock_guard<std::mutex> lock(telemetry_mutex);focused=state==XR_SESSION_STATE_FOCUSED;}
        if(state==XR_SESSION_STATE_READY) {XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};begin.primaryViewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;xrcheck(xrBeginSession(session,&begin),"begin session");running=true;}
        if(state==XR_SESSION_STATE_STOPPING) {running=false;xrcheck(xrEndSession(session),"end session");}
        if(state==XR_SESSION_STATE_EXITING||state==XR_SESSION_STATE_LOSS_PENDING) return false;
      }
      event={XR_TYPE_EVENT_DATA_BUFFER};
    }
    return true;
  }
  bool active()const{return running;}
  void frame() {
    XrFrameWaitInfo wait{XR_TYPE_FRAME_WAIT_INFO};XrFrameState state{XR_TYPE_FRAME_STATE};
    xrcheck(xrWaitFrame(session,&wait,&state),"wait frame");
    XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO};xrcheck(xrBeginFrame(session,&begin),"begin frame");
    std::vector<XrCompositionLayerProjectionView> projection(eyes.size(),{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW});
    bool submit=false;
    if(state.shouldRender) {
      XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO};locate.viewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;locate.displayTime=state.predictedDisplayTime;locate.space=space;
      XrViewState viewstate{XR_TYPE_VIEW_STATE};uint32_t nv=0;std::vector<XrView> views(eyes.size(),{XR_TYPE_VIEW});
      xrcheck(xrLocateViews(session,&locate,&viewstate,views.size(),&nv,views.data()),"locate views");
      submit=nv==eyes.size()&&(viewstate.viewStateFlags&XR_VIEW_STATE_ORIENTATION_VALID_BIT)&&(viewstate.viewStateFlags&XR_VIEW_STATE_POSITION_VALID_BIT);
      if(submit) for(size_t i=0;i<eyes.size();++i) {
        auto& e=eyes[i];uint32_t index;
        XrSwapchainImageAcquireInfo ai{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};xrcheck(xrAcquireSwapchainImage(e.swapchain,&ai,&index),"acquire image");
        XrSwapchainImageWaitInfo wi{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};wi.timeout=XR_INFINITE_DURATION;xrcheck(xrWaitSwapchainImage(e.swapchain,&wi),"wait image");
        glBindFramebuffer(GL_FRAMEBUFFER,fbo);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,e.images[index].image,0);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,e.depth);
        if(glCheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("Incomplete scene framebuffer");
        glViewport(0,0,e.width,e.height);glEnable(GL_DEPTH_TEST);glClearColor(.025f,.035f,.06f,1);glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
        glUseProgram(program);glBindVertexArray(vao);auto matrix=view_projection(views[i]);glUniformMatrix4fv(matrix_location,1,GL_FALSE,matrix.data());
        glDrawArraysInstanced(GL_TRIANGLES,0,36,64);glFlush();
        XrSwapchainImageReleaseInfo ri{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};xrcheck(xrReleaseSwapchainImage(e.swapchain,&ri),"release image");
        projection[i].pose=views[i].pose;projection[i].fov=views[i].fov;projection[i].subImage.swapchain=e.swapchain;
        projection[i].subImage.imageRect={{0,0},{static_cast<int32_t>(e.width),static_cast<int32_t>(e.height)}};
      }
    }
    XrCompositionLayerProjection layer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};layer.space=space;layer.viewCount=projection.size();layer.views=projection.data();
    const XrCompositionLayerBaseHeader* layers[]={reinterpret_cast<const XrCompositionLayerBaseHeader*>(&layer)};
    XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};end.displayTime=state.predictedDisplayTime;end.environmentBlendMode=XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
    end.layerCount=submit?1:0;end.layers=submit?layers:nullptr;xrcheck(xrEndFrame(session,&end),"end frame");
    float hz=0;if(get_refresh) get_refresh(session,&hz);
    XrPerformanceMetricsCounterMETA cpu{XR_TYPE_PERFORMANCE_METRICS_COUNTER_META},gpu=cpu,drop=cpu;
    bool frame_valid=metrics&&cpu_path&&gpu_path&&XR_SUCCEEDED(query(session,cpu_path,&cpu))&&XR_SUCCEEDED(query(session,gpu_path,&gpu))&&
      (cpu.counterFlags&XR_PERFORMANCE_METRICS_COUNTER_FLOAT_VALUE_VALID_BIT_META)&&(gpu.counterFlags&XR_PERFORMANCE_METRICS_COUNTER_FLOAT_VALUE_VALID_BIT_META)&&
      cpu.counterUnit==XR_PERFORMANCE_METRICS_COUNTER_UNIT_MILLISECONDS_META&&gpu.counterUnit==XR_PERFORMANCE_METRICS_COUNTER_UNIT_MILLISECONDS_META;
    bool drop_valid=metrics&&drop_path&&XR_SUCCEEDED(query(session,drop_path,&drop))&&(drop.counterFlags&XR_PERFORMANCE_METRICS_COUNTER_UINT_VALUE_VALID_BIT_META);
    std::lock_guard<std::mutex> lock(telemetry_mutex);refresh=hz;
    if(submit&&focused) {
      ++rendered;
      if(frame_valid&&frame_ms.size()<100000) frame_ms.push_back(std::max(cpu.floatValue,gpu.floatValue));
      else invalid_metrics=true;
      if(drop_valid) { dropped=drop.uintValue;if(dropped_start<0) dropped_start=dropped; }
      else invalid_metrics=true;
    }
  }
};
}
nlohmann::json scene_snapshot(bool reset, bool include_frames) {
  std::lock_guard<std::mutex> lock(telemetry_mutex);
  if(reset) {frame_ms.clear();rendered=0;dropped_start=dropped;invalid_metrics=false;}
  nlohmann::json rate=nullptr;
  if(!invalid_metrics&&dropped_start>=0&&dropped>=dropped_start&&rendered>0) {
    double missing=double(dropped-dropped_start);rate=missing/(rendered+missing);
  }
  return {{"ready",ready},{"focused",focused},{"refresh_hz",refresh},{"error",error},
    {"frame_ms",include_frames ? nlohmann::json(frame_ms) : nlohmann::json::array()},{"rendered_frames",rendered},{"missed_frame_rate",rate},
    {"frame_metric_source","XR_META_performance_metrics: max(app CPU, app GPU)"},
    {"missed_frame_source","compositor dropped_frame_count delta / (submitted + dropped)"}};
}
}
void android_main(android_app* app) {
  try {
    xrgpu::Scene scene;scene.init(app);
    while(!app->destroyRequested) {
      int events;android_poll_source* source=nullptr;
      while(ALooper_pollOnce(scene.active()?0:10,nullptr,&events,reinterpret_cast<void**>(&source))>=0) {
        if(source) source->process(app,source);
        if(app->destroyRequested) break;
      }
      if(app->destroyRequested||!scene.events()) break;
      if(scene.active()) scene.frame();
    }
  } catch(const std::exception& e) {
    std::lock_guard<std::mutex> lock(xrgpu::telemetry_mutex);xrgpu::error=e.what();
  }
  ANativeActivity_finish(app->activity);
}
