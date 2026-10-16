cmake_minimum_required(VERSION 3.24)
project(xrGPU_benchmarks LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
get_filename_component(ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(EXECUTORCH_ROOT "${ROOT}/artifacts/deps/executorch")
set(PYTHON_EXECUTABLE "${ROOT}/.venv/bin/python" CACHE FILEPATH "Export environment")
foreach(option VULKAN EXTENSION_MODULE EXTENSION_TENSOR EXTENSION_DATA_LOADER EXTENSION_NAMED_DATA_MAP DEVTOOLS)
  set(EXECUTORCH_BUILD_${option} ON CACHE BOOL "" FORCE)
endforeach()
set(EXECUTORCH_ENABLE_EVENT_TRACER ON CACHE BOOL "" FORCE)
set(EXECUTORCH_BUILD_XNNPACK OFF CACHE BOOL "" FORCE)
set(EXECUTORCH_BUILD_KERNELS_OPTIMIZED OFF CACHE BOOL "" FORCE)
set(EXECUTORCH_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(EXECUTORCH_BUILD_EXECUTOR_RUNNER OFF CACHE BOOL "" FORCE)
set(EXECUTORCH_VULKAN_SHADER_COMPILE_NTHREADS 4 CACHE STRING "" FORCE)
add_subdirectory("${EXECUTORCH_ROOT}" et)
target_include_directories(vulkan_backend PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_API_LAYERS OFF CACHE BOOL "" FORCE)
set(BUILD_LOADER ON CACHE BOOL "" FORCE)
set(DYNAMIC_LOADER ON CACHE BOOL "" FORCE)
add_subdirectory("${ROOT}/artifacts/deps/openxr" xr)
add_library(xrgpu_benchmarks SHARED runner.cpp scene.cpp
  "${ANDROID_NDK}/sources/android/native_app_glue/android_native_app_glue.c")
target_include_directories(xrgpu_benchmarks PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${ROOT}"
  "${ROOT}/artifacts/deps" "${EXECUTORCH_ROOT}/third-party/json/single_include"
  "${EXECUTORCH_ROOT}/backends/vulkan/third-party/Vulkan-Headers/include"
  "${ANDROID_NDK}/sources/android/native_app_glue")
target_compile_definitions(xrgpu_benchmarks PRIVATE XR_USE_PLATFORM_ANDROID XR_USE_GRAPHICS_API_OPENGL_ES
  XRGPU_ET_REVISION="${XRGPU_ET_REVISION}" XRGPU_RUNNER_REVISION="${XRGPU_RUNNER_REVISION}")
target_link_libraries(xrgpu_benchmarks PRIVATE executorch extension_module extension_tensor portable_ops_lib
  vulkan_backend openxr_loader android log EGL GLESv3 dl)
target_link_options(xrgpu_benchmarks PRIVATE "-Wl,-u,ANativeActivity_onCreate" "-Wl,-z,max-page-size=16384")
