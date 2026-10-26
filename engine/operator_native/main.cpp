#include <xrgpu/operators.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <stdexcept>

using Json = nlohmann::json;
namespace fs = std::filesystem;
namespace op = xrgpu::operators;
using Clock = std::chrono::steady_clock;

namespace {
Json read_json(const fs::path& path) {
  std::ifstream in(path); if (!in) throw std::runtime_error("Missing operator JSON artifact");
  Json value; in >> value; return value;
}
void save(const fs::path& path, const Json& value) {
  auto staging = path.string() + ".partial";
  { std::ofstream out(staging); out << value.dump() << '\n';
    if (!out) throw std::runtime_error("Could not preserve native operator evidence"); }
  fs::rename(staging, path);
}
fs::path contained(const fs::path& root, const std::string& name) {
  fs::path relative(name);
  if (name.empty() || relative.is_absolute() || name.find('\\') != std::string::npos)
    throw std::runtime_error("Invalid operator tensor path");
  fs::path path = root;
  for (const auto& part : relative) {
    if (part == "." || part == ".." || part.empty()) throw std::runtime_error("Invalid operator tensor path");
    path /= part;
    if (fs::is_symlink(path)) throw std::runtime_error("Operator artifact links are forbidden");
  }
  return path;
}
std::vector<uint8_t> read_bytes(const fs::path& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in || in.tellg() < 0 || in.tellg() > 64 * 1024 * 1024) throw std::runtime_error("Invalid operator tensor file");
  std::vector<uint8_t> bytes(static_cast<size_t>(in.tellg()));
  in.seekg(0); in.read(reinterpret_cast<char*>(bytes.data()), bytes.size());
  if (!in) throw std::runtime_error("Incomplete operator tensor file");
  return bytes;
}
std::vector<op::Tensor> read_tensors(const fs::path& root, const Json& values) {
  std::vector<op::Tensor> result;
  for (const auto& spec : values) {
    if (spec.is_null()) throw std::runtime_error("This operator implementation does not accept omitted input slots");
    op::Tensor tensor;
    tensor.name = spec.at("name").get<std::string>();
    tensor.dtype = op::parse_dtype(spec.at("dtype").get<std::string>());
    tensor.shape = spec.at("shape").get<std::vector<int64_t>>();
    tensor.data = read_bytes(contained(root, spec.at("file").get<std::string>()));
    result.push_back(std::move(tensor));
  }
  return result;
}
bool compare(const std::vector<op::Tensor>& a, const std::vector<op::Tensor>& b, const Json& rules) {
  if (a.size() != b.size()) return false;
  const double atol = rules.at("atol"), rtol = rules.at("rtol");
  for (size_t i = 0; i < a.size(); ++i) {
    if (a[i].name != b[i].name || a[i].shape != b[i].shape || a[i].dtype != b[i].dtype || a[i].data.size() != b[i].data.size()) return false;
    if (a[i].dtype != op::DType::Float32) {
      if (a[i].data != b[i].data) return false;
      continue;
    }
    if (a[i].data.size() % sizeof(float)) return false;
    for (size_t j = 0; j < a[i].data.size(); j += sizeof(float)) {
      float x, y;
      std::memcpy(&x, a[i].data.data() + j, sizeof(float));
      std::memcpy(&y, b[i].data.data() + j, sizeof(float));
      if (!std::isfinite(x) || !std::isfinite(y) || std::abs(double(x) - double(y)) > atol + rtol * std::abs(double(y))) return false;
    }
  }
  return true;
}
Json write_outputs(const fs::path& root, const std::string& phase, const std::vector<op::Tensor>& tensors) {
  Json descriptors = Json::array();
  for (size_t i = 0; i < tensors.size(); ++i) {
    const auto& tensor = tensors[i];
    auto name = phase + ".output" + std::to_string(i) + ".bin";
    std::ofstream out(root / name, std::ios::binary);
    out.write(reinterpret_cast<const char*>(tensor.data.data()), tensor.data.size());
    if (!out) throw std::runtime_error("Could not preserve operator output");
    descriptors.push_back({{"name", tensor.name}, {"dtype", op::dtype_name(tensor.dtype)},
                           {"shape", tensor.shape}, {"file", name}});
  }
  return descriptors;
}
}

int main(int argc, char** argv) {
  if (argc != 2) { std::fprintf(stderr, "Usage: xrbench-operators JOB.json\n"); return 2; }
  Json evidence; fs::path output;
  try {
    const auto job = read_json(argv[1]);
    if (job.at("kind") != "xrbench.operator-job" || job.at("schema_version") != 1)
      throw std::runtime_error("Unsupported native operator job");
    const int warmups = job.at("warmups"), samples = job.at("samples"), seconds = job.at("timeout_seconds");
    if (warmups < 0 || warmups > 100 || samples < 1 || samples > 1000 || seconds < 5 || seconds > 300)
      throw std::runtime_error("Invalid native operator runtime limits");
    output = job.at("output").get<std::string>(); fs::create_directories(output);
    const auto suite_root = fs::path(job.at("suite").get<std::string>());
    const auto suite = read_json(suite_root / "suite.json");
    evidence = {{"kind", "xrbench.operator-evidence"}, {"schema_version", 1},
                {"backend", "xrgpu_vulkan"}, {"status", "running"},
                {"suite_revision", suite.at("suite_revision")}, {"results", Json::array()},
                {"device", Json::parse(op::probe())}};
    save(output / "native.json", evidence);
    const auto deadline = Clock::now() + std::chrono::seconds(seconds);
    auto check = [&]() {
      if (fs::exists(output / "cancel")) throw std::runtime_error("Operator run canceled");
      if (Clock::now() > deadline) throw std::runtime_error("Operator runtime limit exceeded");
    };
    for (const auto& spec : suite.at("cases")) {
      check(); const auto id = spec.at("id").get<std::string>();
      const auto case_root = contained(suite_root, id); const auto result_root = contained(output, id);
      fs::create_directories(result_root);
      Json row = {{"case", id}, {"status", "interrupted"}, {"samples", Json::array()}};
      evidence["results"].push_back(row); auto& current = evidence["results"].back();
      save(output / "native.json", evidence);
      try {
        if (!spec.at("attributes").empty()) throw std::runtime_error("This implementation does not accept operator attributes");
        auto inputs = read_tensors(case_root, spec.at("inputs"));
        auto expected = read_tensors(case_root, spec.at("outputs"));
        op::Options options; options.profiling = job.value("profiling", false);
        auto model = op::Operator::prepare(job.at("package").get<std::string>(), spec.at("operator").get<std::string>(), inputs, options);
        auto prepared = Json::parse(model->info());
        if (prepared.at("backend") != "vulkan" && prepared.at("backend") != "xrgpu_vulkan")
          throw std::runtime_error("Operator runner requires Vulkan; fallback cannot qualify");
        check(); model->run(); auto before = model->read_outputs();
        current["outputs_before"] = write_outputs(result_root, "before", before);
        if (!compare(before, expected, spec.at("comparison"))) throw std::runtime_error("Operator correctness failed before measurement");
        for (int i = 0; i < warmups; ++i) { check(); model->run(); }
        for (int i = 0; i < samples; ++i) {
          check(); const auto begin = Clock::now(); model->run();
          const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
          const auto info = Json::parse(model->info());
          Json sample = {{"execution_ms", elapsed}, {"model_ms", info.value("execution_ms", Json(nullptr))},
                         {"gpu_ms", info.value("gpu_ms", Json(nullptr))}, {"dispatch_count", info.value("dispatch_count", Json(nullptr))}};
          current["samples"].push_back(sample);
          if (i % 10 == 0) save(output / "native.json", evidence);
        }
        auto after = model->read_outputs(); current["outputs_after"] = write_outputs(result_root, "after", after);
        current["operator_info"] = Json::parse(model->info());
        if (!compare(after, expected, spec.at("comparison"))) throw std::runtime_error("Operator correctness failed after measurement");
        current["status"] = "complete";
      } catch (const std::exception& error) {
        current["status"] = "failed"; current["reason"] = error.what(); save(output / "native.json", evidence);
        if (fs::exists(output / "cancel") || Clock::now() > deadline) throw;
      }
      save(output / "native.json", evidence);
    }
    evidence["status"] = "complete"; save(output / "native.json", evidence); return 0;
  } catch (const std::exception& error) {
    evidence["status"] = !output.empty() && fs::exists(output / "cancel") ? "canceled" : "failed";
    evidence["error"] = error.what();
    if (!output.empty()) try { save(output / "native.json", evidence); } catch (...) {}
    std::fprintf(stderr, "%s\n", error.what()); return 1;
  }
}
