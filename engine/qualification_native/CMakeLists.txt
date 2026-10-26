cmake_minimum_required(VERSION 3.24)
project(xrBenchQualification LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(XRGPU_SOURCE_ROOT "" CACHE PATH "xrGPU source checkout")
if(NOT EXISTS "${XRGPU_SOURCE_ROOT}/runtime/include/xrgpu/session.h" OR NOT XRGPU_KERNEL_PACKAGE_ROOT)
  message(FATAL_ERROR "Provide xrGPU source and the verified compiled package")
endif()
set(XRGPU_BUILD_DEMO OFF CACHE BOOL "" FORCE)
set(XRGPU_ENABLE_EXPERIMENTS ON CACHE BOOL "" FORCE)
set(XRGPU_PREBUILT_RUNTIME "" CACHE FILEPATH "Optional existing Android development core for the exact package")
if(XRGPU_PREBUILT_RUNTIME)
  # Reuse a validated native build during local qualification without compiling
  # a second ExecuTorch checkout. The runner still links the same shared core.
  file(READ "${XRGPU_KERNEL_PACKAGE_ROOT}/manifest.json" manifest)
  string(JSON package_digest GET "${manifest}" package_sha256)
  execute_process(COMMAND "${PYTHON_EXECUTABLE}" -c
    "import pathlib,sys;assert sys.argv[2].encode() in pathlib.Path(sys.argv[1]).read_bytes(), 'Prebuilt core package differs'"
    "${XRGPU_PREBUILT_RUNTIME}" "${package_digest}" RESULT_VARIABLE prebuilt_status)
  if(NOT prebuilt_status EQUAL 0)
    message(FATAL_ERROR "Prebuilt xrGPU core does not contain the verified package identity")
  endif()
  add_library(xrgpu::runtime SHARED IMPORTED GLOBAL)
  set_target_properties(xrgpu::runtime PROPERTIES IMPORTED_LOCATION "${XRGPU_PREBUILT_RUNTIME}"
    INTERFACE_INCLUDE_DIRECTORIES "${XRGPU_SOURCE_ROOT}/runtime/include")
else()
  add_subdirectory("${XRGPU_SOURCE_ROOT}" xrgpu)
endif()
add_library(xrbench_qualification_logic STATIC runner.cpp)
set_target_properties(xrbench_qualification_logic PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(xrbench_qualification_logic PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}"
  "${XRGPU_SOURCE_ROOT}/artifacts/deps/executorch/third-party/json/single_include")
target_link_libraries(xrbench_qualification_logic PUBLIC xrgpu::runtime)
add_executable(xrgpu-qualification-runner main.cpp)
target_link_libraries(xrgpu-qualification-runner PRIVATE xrbench_qualification_logic)
set_target_properties(xrgpu-qualification-runner PROPERTIES BUILD_RPATH "$ORIGIN")

option(XRBENCH_QUALIFICATION_XR "Build the qualification-only Android OpenXR runner" OFF)
if(XRBENCH_QUALIFICATION_XR)
  if(NOT ANDROID)
    message(FATAL_ERROR "The OpenXR qualification activity requires Android")
  endif()
  set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
  set(BUILD_API_LAYERS OFF CACHE BOOL "" FORCE)
  set(BUILD_LOADER ON CACHE BOOL "" FORCE)
  set(DYNAMIC_LOADER ON CACHE BOOL "" FORCE)
  add_subdirectory("${XRGPU_SOURCE_ROOT}/artifacts/deps/openxr" openxr)
  add_library(xrgpu_qualification SHARED xr_jni.cpp "../native/scene.cpp"
    "${ANDROID_NDK}/sources/android/native_app_glue/android_native_app_glue.c")
  target_include_directories(xrgpu_qualification PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}"
    "${XRGPU_SOURCE_ROOT}/artifacts/deps"
    "${ANDROID_NDK}/sources/android/native_app_glue")
  target_compile_definitions(xrgpu_qualification PRIVATE XR_USE_PLATFORM_ANDROID XR_USE_GRAPHICS_API_OPENGL_ES)
  target_link_libraries(xrgpu_qualification PRIVATE xrbench_qualification_logic openxr_loader
    android log EGL GLESv3 dl)
  target_link_options(xrgpu_qualification PRIVATE "-Wl,-u,ANativeActivity_onCreate" "-Wl,-z,max-page-size=16384")
endif()
