#pragma once
#include <algorithm>
#include <cstddef>
#if defined(__aarch64__)
#include <arm_neon.h>
#endif

namespace xrbench_cpu {
// Row-major FP32 reference and vector candidate. Tail columns use scalar work.
// One calling thread; allocation, input preparation and output copy are external.
inline void matmul(const float* a, const float* b, float* c,
                   size_t m, size_t k, size_t n, bool candidate) {
  std::fill(c, c + m * n, 0.f);
  for (size_t i = 0; i < m; ++i) {
    for (size_t p = 0; p < k; ++p) {
      const float av = a[i * k + p];
      size_t j = 0;
#if defined(__aarch64__)
      if (candidate) {
        const auto v = vdupq_n_f32(av);
        for (; j + 4 <= n; j += 4) {
          const auto sum = vfmaq_f32(vld1q_f32(c + i*n + j), v, vld1q_f32(b + p*n + j));
          vst1q_f32(c + i*n + j, sum);
        }
      }
#else
      (void)candidate;
#endif
      for (; j < n; ++j) c[i*n+j] += av * b[p*n+j];
    }
  }
}
}
