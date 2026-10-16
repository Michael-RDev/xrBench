#pragma once
#include <cstdint>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

namespace xrgpu_benchmarks {
// Bound allocation before multiplying dimensions or copying JNI buffers.
inline size_t validate_tensor(const std::vector<int32_t>& shape,
                              const std::string& dtype, size_t bytes) {
  if (shape.empty() || shape.size() > 8) throw std::runtime_error("Invalid rank");
  size_t elements = 1;
  for (int32_t size : shape) {
    if (size < 1 || elements > 16777216 / static_cast<size_t>(size))
      throw std::runtime_error("Invalid shape");
    elements *= size;
  }
  if (dtype != "float32" && dtype != "int64") throw std::runtime_error("Unsupported input dtype");
  if (bytes != elements * (dtype == "int64" ? 8 : 4)) throw std::runtime_error("Input byte length mismatch");
  return elements;
}
}
