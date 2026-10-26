#pragma once
#include <functional>
#include <string>
#include <nlohmann/json.hpp>

namespace xrbench_qualification {
// An Android OpenXR activity supplies real scene telemetry. The shell runner
// deliberately cannot manufacture sustained XR evidence without this callback.
using SceneProbe = std::function<nlohmann::json(bool reset, bool include_frames)>;
int execute_file(const std::string& job_path, SceneProbe scene = {});
}
