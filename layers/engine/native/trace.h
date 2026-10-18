#pragma once
#include <executorch/runtime/core/event_tracer.h>
#include <executorch/runtime/platform/platform.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <vector>

namespace xrgpu {
using Json = nlohmann::json;
inline double milliseconds() {
  return std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}

// GPU events come from real Vulkan timestamp queries emitted by ET. Host
// events use a separate clock and are never presented as GPU execution time.
class Trace final : public executorch::runtime::EventTracer {
  using Entry = executorch::runtime::EventTracerEntry;
  using Tensor = executorch::aten::Tensor;
  using EValue = executorch::runtime::EValue;
  using Result = executorch::runtime::Result<bool>;
  struct HostEvent { std::string name; double start; };
  std::vector<HostEvent> host_;
 public:
  Json gpu = Json::array(), host = Json::array(), cpu_delegate = Json::array();
  bool invalid_gpu_timestamp=false;
  void reset() { gpu.clear(); host.clear(); cpu_delegate.clear(); host_.clear(); invalid_gpu_timestamp=false; }
  void create_event_block(const char*) override {}
  Entry start_profiling(const char* name, int32_t = -1, uint32_t = 0) override {
    host_.push_back({name ? name : "unnamed", milliseconds()});
    Entry e{}; e.event_id = static_cast<int64_t>(host_.size() - 1); return e;
  }
  Entry start_profiling_delegate(const char* name, int32_t) override {
    return start_profiling(name);
  }
  void end_profiling(Entry e) override {
    const auto& h = host_.at(e.event_id);
    host.push_back({{"name", h.name}, {"ms", milliseconds() - h.start}});
  }
  void end_profiling_delegate(Entry e, const void* = nullptr, size_t = 0) override {
    end_profiling(e);
  }
  void log_profiling_delegate(const char* name, int32_t, et_timestamp_t start,
      et_timestamp_t end, const void* = nullptr, size_t = 0) override {
    if (end < start) {invalid_gpu_timestamp=true;return;}
    std::string label = name ? name : "unnamed";
    // The pinned Vulkan delegate labels shader timestamp queries with a dispatch
    // id. XNNPACK uses the same callback for CPU timings in PAL clock ticks.
    if (label.find("\"dispatch_id\":") != std::string::npos) {
      auto event=Json::parse(label,nullptr,false);
      if(event.is_discarded()||!event.is_object())event=Json{{"name",label}};
      event.update({{"start_ns",start},{"end_ns",end},{"ms",double(end-start)/1e6}});
      gpu.push_back(event);
    } else {
      auto scale = executorch::runtime::pal_ticks_to_ns_multiplier();
      cpu_delegate.push_back({{"name", label},
          {"ms", double(end - start) * scale.numerator / scale.denominator / 1e6}});
    }
  }
  void track_allocation(uint32_t, size_t) override {}
  uint32_t track_allocator(const char*) override { return 0; }
  Result log_evalue(const EValue&, executorch::runtime::LoggedEValueType) override { return false; }
  Result log_intermediate_output_delegate(const char*, int32_t, const Tensor&) override { return false; }
  Result log_intermediate_output_delegate(const char*, int32_t,
      const executorch::aten::ArrayRef<Tensor>) override { return false; }
  Result log_intermediate_output_delegate(const char*, int32_t, const int&) override { return false; }
  Result log_intermediate_output_delegate(const char*, int32_t, const bool&) override { return false; }
  Result log_intermediate_output_delegate(const char*, int32_t, const double&) override { return false; }
  void set_delegation_intermediate_output_filter(executorch::runtime::EventTracerFilterBase*) override {}
};
}
