cmake_minimum_required(VERSION 3.24)
project(xrBenchOperators LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(XRGPU_OPERATOR_PACKAGE "" CACHE PATH "Verified native operator package")
set(NLOHMANN_JSON_INCLUDE "" CACHE PATH "Pinned JSON header directory")
if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "arm64-v8a")
  message(FATAL_ERROR "The operator benchmark runner targets native Android arm64-v8a")
endif()
if(NOT EXISTS "${XRGPU_OPERATOR_PACKAGE}/include/xrgpu/operators.h" OR
   NOT EXISTS "${XRGPU_OPERATOR_PACKAGE}/lib/android-arm64/libxrgpu_operators.so" OR
   NOT EXISTS "${NLOHMANN_JSON_INCLUDE}/nlohmann/json.hpp")
  message(FATAL_ERROR "Provide the verified operator package and pinned JSON headers")
endif()
add_library(xrgpu_operators SHARED IMPORTED)
set_target_properties(xrgpu_operators PROPERTIES
  IMPORTED_LOCATION "${XRGPU_OPERATOR_PACKAGE}/lib/android-arm64/libxrgpu_operators.so"
  INTERFACE_INCLUDE_DIRECTORIES "${XRGPU_OPERATOR_PACKAGE}/include")
add_executable(xrbench-operators main.cpp)
target_include_directories(xrbench-operators PRIVATE "${NLOHMANN_JSON_INCLUDE}")
target_link_libraries(xrbench-operators PRIVATE xrgpu_operators)
target_link_options(xrbench-operators PRIVATE "-Wl,-z,max-page-size=16384")
