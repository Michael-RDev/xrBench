#pragma once
#include <nlohmann/json.hpp>
#include <filesystem>
#include <functional>

namespace xrbench {
struct BenchmarkHooks {
    // Used by the host-only verification adapter. Android never supplies hooks.
    std::function<void(const nlohmann::json&)> checkpoint;
    std::function<double()> elapsed_ms;
};
nlohmann::json benchmark(const std::filesystem::path& root,
                         const nlohmann::json& config,
                         const std::filesystem::path& output,
                         const nlohmann::json& device,
                         const BenchmarkHooks& hooks = {});
nlohmann::json probe();
}
