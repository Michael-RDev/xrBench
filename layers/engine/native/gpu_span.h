#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <optional>

namespace xrgpu {
// Only finish after independently verifying that all events share one graph/queue.
struct GpuSpan {
  uint64_t first=UINT64_MAX,last=0,previous=0;
  bool seen=false,invalid=false;
  void add(uint64_t start,uint64_t end) {
    invalid|=end<start||(seen&&start<previous); // Includes timestamp wrap between events.
    first=std::min(first,start);last=std::max(last,end);previous=start;seen=true;
  }
  std::optional<double> finish(size_t executions,bool invalidTrace,double scale=1) const {
    if(executions!=1||invalidTrace||invalid||!seen||last<=first||!std::isfinite(scale)||scale<=0)return {};
    return double(last-first)*scale/1e6;
  }
};
}
