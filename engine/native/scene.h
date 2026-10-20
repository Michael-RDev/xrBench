#pragma once
#include <nlohmann/json.hpp>
namespace xrgpu {
nlohmann::json scene_snapshot(bool reset, bool include_frames = true);
}
