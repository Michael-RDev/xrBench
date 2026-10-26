#pragma once
#include <nlohmann/json.hpp>
#include <filesystem>

namespace xrbench {
nlohmann::json benchmark(const std::filesystem::path& root,
                         const nlohmann::json& config,
                         const std::filesystem::path& output,
                         const nlohmann::json& device);
nlohmann::json probe();
}
