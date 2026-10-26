#include "runner.h"
#include <xrgpu/session.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <random>
#include <regex>
#include <stdexcept>
#include <thread>

namespace xrbench_qualification {
using Json = nlohmann::json;
namespace {
using Clock = std::chrono::steady_clock;
double milliseconds() {
  return std::chrono::duration<double, std::milli>(Clock::now().time_since_epoch()).count();
}
Json read_json(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("Missing JSON file: " + path.string());
  Json result; input >> result; return result;
}
void save(const std::filesystem::path& path, const Json& report) {
  auto temporary = path.string() + ".partial";
  { std::ofstream stream(temporary); stream << report.dump() << '\n';
    if (!stream) throw std::runtime_error("Could not preserve native evidence"); }
  std::filesystem::rename(temporary, path);
}
std::vector<uint8_t> read_bytes(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input || input.tellg() < 0) throw std::runtime_error("Missing tensor fixture");
  std::vector<uint8_t> value(static_cast<size_t>(input.tellg()));
  input.seekg(0); input.read(reinterpret_cast<char*>(value.data()), value.size());
  if (!input) throw std::runtime_error("Incomplete tensor fixture");
  return value;
}
std::vector<xrgpu::Tensor> tensors(const std::filesystem::path& bundle, const Json& specs) {
  std::vector<xrgpu::Tensor> result;
  for (const auto& spec : specs) {
    auto name = spec.at("file").get<std::string>();
    if (name.empty() || name[0] == '/' || name.find("..") != std::string::npos)
      throw std::runtime_error("Invalid fixture path");
    xrgpu::Tensor value;
    value.name = spec.at("name").get<std::string>();
    value.shape = spec.at("shape").get<std::vector<int64_t>>();
    auto dtype = spec.at("dtype").get<std::string>();
    if (dtype != "float32" && dtype != "int64") throw std::runtime_error("Unsupported fixture dtype");
    value.dtype = dtype == "float32" ? xrgpu::DType::Float32 : xrgpu::DType::Int64;
    value.data = read_bytes(bundle / name);
    result.push_back(std::move(value));
  }
  return result;
}
Json compare(const std::vector<xrgpu::Tensor>& actual, const std::vector<xrgpu::Tensor>& expected) {
  bool passed = actual.size() == expected.size(); double max_error = 0;
  if (passed) for (size_t i=0; i<actual.size(); ++i) {
    const auto& a=actual[i]; const auto& b=expected[i];
    if (a.shape != b.shape || a.dtype != b.dtype || a.data.size() != b.data.size()) { passed=false; break; }
    if (a.dtype == xrgpu::DType::Int64) { passed = passed && a.data == b.data; continue; }
    if (a.data.size() % sizeof(float)) { passed=false; break; }
    for (size_t j=0; j<a.data.size(); j+=sizeof(float)) {
      float x,y; std::memcpy(&x,a.data.data()+j,sizeof(float)); std::memcpy(&y,b.data.data()+j,sizeof(float));
      double error=std::abs(double(x)-double(y)); max_error=std::max(max_error,error);
      if (!std::isfinite(x) || !std::isfinite(y) || error>1e-4+1e-4*std::abs(double(y))) passed=false;
    }
  }
  return {{"passed",passed},{"max_abs_error",max_error},{"atol",1e-4},{"rtol",1e-4}};
}
std::string command(const char* text) {
  std::string out; FILE* pipe=popen(text,"r"); if (!pipe) return out;
  char data[512]; while (fgets(data,sizeof(data),pipe)) out+=data;
  pclose(pipe); return out;
}
Json conditions(const SceneProbe& scene) {
  if (scene) {
    auto value=scene(false,false).at("conditions");
    if (!value.at("thermal_status").is_number_integer() || !value.at("charging").is_boolean())
      throw std::runtime_error("Android power observations unavailable");
    if (value.at("thermal_status").get<int>()>=3) throw std::runtime_error("Thermal state severe; stop qualification");
    return value;
  }
  auto thermal=command("dumpsys thermalservice"); std::smatch match;
  if (!std::regex_search(thermal,match,std::regex("Thermal Status: ([0-9]+)")))
    throw std::runtime_error("Native shell cannot read Android thermal status");
  int status=std::stoi(match[1]); if (status>=3) throw std::runtime_error("Thermal state severe; stop qualification");
  auto battery=command("dumpsys battery");
  if (battery.find("powered:")==std::string::npos) throw std::runtime_error("Battery charging status unavailable");
  bool charging=std::regex_search(battery,std::regex("(AC|USB|Wireless|Dock) powered: true"));
  return {{"thermal_status",status},{"charging",charging}};
}
void ensure_vulkan(const Json& info) {
  auto backend=info.at("backend").get<std::string>();
  if (backend!="vulkan" && backend!="executorch_vulkan")
    throw std::runtime_error("Session fell back to CPU; GPU qualification unavailable");
}
Json decisions(const Json& info) {
  Json result=Json::array();
  const auto& values=info.at("decisions");
  if (values.is_array()) return values;
  for (const auto& item:values.items()) result.push_back(item.value());
  return result;
}
void observe(Json& report,const Json& info,const Json& shader_hashes) {
  ensure_vulkan(info); report["target"]=info.at("device");
  if(info.at("kernel_package").at("package_sha256")!=report.at("package_sha256"))
    throw std::runtime_error("Loaded native core contains another kernel package");
  report["decisions"]=decisions(info);
  Json entries=Json::array();
  if (report.at("profiling")=="trace") for (const auto& event:info.at("trace").at("gpu")) {
    Json e=Json::parse(event.at("name").get<std::string>());
    auto name=e.at("kernel_name").get<std::string>();
    e["shader_origin"]=shader_hashes.contains(name)?"xrgpu":"stock";
    if(shader_hashes.contains(name))e["spirv_sha256"]=shader_hashes.at(name);
    e["ms"]=event.at("ms"); entries.push_back(e);
  }
  report["dispatches"]=entries;
}
void check(const Json& job,double deadline) {
  if (milliseconds()>deadline) throw std::runtime_error("Bounded qualification deadline exceeded");
  if (std::filesystem::exists(std::filesystem::path(job.at("output").get<std::string>()).parent_path()/"cancel"))
    throw std::runtime_error("Qualification canceled; original partial evidence preserved");
}
std::string experiment(const Json& job) {
  if (!job.contains("request") || job.at("request").is_null()) return "";
  auto op=job.at("request").at("operation").get<std::string>();
  return op=="linear" ? "linear:"+job.at("variant").get<std::string>() : op;
}
}

int execute_file(const std::string& job_path,SceneProbe scene) {
  Json report,job; std::filesystem::path output;
  try {
    job=read_json(job_path); report=job; output=job.at("output").get<std::string>();
    report.erase("rules"); report.erase("shader_hashes"); report.erase("bundle"); report.erase("output");
    report.update({{"source","native_headset"},{"execution_target","headset"},{"synthetic",false},
                   {"backend","executorch_vulkan"},{"status","running"},{"pairs",Json::array()},
                   {"decisions",Json::array()},{"dispatches",Json::array()}});
    auto bundle=std::filesystem::path(job.at("bundle").get<std::string>());
    auto manifest=read_json(bundle/"bundle.json"); Json fixture;
    for (const auto& f:manifest.at("fixtures")) if (f.at("seed")==job.at("fixture_seed")) fixture=f;
    if (fixture.is_null()) throw std::runtime_error("No fixture for planned seed");
    auto input=tensors(bundle,fixture.at("inputs")), expected=tensors(bundle,fixture.at("outputs"));
    const auto stage=job.at("stage").get<std::string>();
    xrgpu::SessionOptions base, alternative;
    const bool final_rules = stage=="correctness" || stage=="model" || stage=="sustained";
    base.experimental_repairs=true;
    // Final model tests must use exactly the rules that a production Session
    // will receive. Blanket repairs can otherwise hide missing eligible paths.
    alternative.experimental_repairs=!final_rules;
    report["selection_policy"]=final_rules ? "exact_rules" : "development";
    base.profiling=alternative.profiling=job.at("profiling")=="trace";
    alternative.experiment=stage=="noise" ? "" : experiment(job);
    if (job.contains("rules") && !job.at("rules").empty() && stage!="noise")
      alternative.experimental_rules=job.at("rules").dump();
    auto baseline=xrgpu::Session::load(bundle.string(),base);
    auto candidate=xrgpu::Session::load(bundle.string(),alternative);
    ensure_vulkan(Json::parse(baseline->info())); ensure_vulkan(Json::parse(candidate->info()));
    const auto protocol=job.at("protocol"); int warmups=protocol.at("warmups"), count=protocol.at("samples"), pairs=protocol.at("pairs");
    report["warmups"]=warmups;
    if (warmups<20 || count<100 || pairs<10) throw std::runtime_error("Qualification protocol was weakened");
    double deadline=milliseconds()+job.value("timeout_seconds",7200)*1000.;
    auto shader_hashes=job.at("shader_hashes");
    if (stage=="incorrect") {
      auto result=candidate->forward(input);
      if (result.empty() || result[0].data.empty()) throw std::runtime_error("Missing candidate output");
      if (result[0].dtype==xrgpu::DType::Float32) {float wrong=1e20f;std::memcpy(result[0].data.data(),&wrong,sizeof(float));}
      else result[0].data[0]^=0xff;
      report["incorrect_rejected"]=!compare(result,expected).at("passed").get<bool>();
      report["validation_control"]="candidate_output_perturbation";
      observe(report,Json::parse(candidate->info()),shader_hashes);
    } else if(stage=="sustained") {
      if (!scene) throw std::runtime_error("Sustained XR stage requires the qualification Android activity");
      report["frames"]=Json::object(); report["missed_frame_rate"]=Json::object();
      report["arm_duration_seconds"]=Json::object();
      int seconds=protocol.at("sustained_seconds"); if(seconds<600)throw std::runtime_error("Ten-minute XR arms required");
      bool lost=false;
      std::vector<int> arms={0,1};
      auto identity=job.at("run_id").get<std::string>(); std::seed_seq sequence(identity.begin(),identity.end());
      std::mt19937 random(sequence);std::shuffle(arms.begin(),arms.end(),random);
      report["order"]=arms[0]==0?Json({"baseline","comparison"}):Json({"comparison","baseline"});
      for(int arm:arms){
        auto& model=arm==0?baseline:candidate; std::string label=arm==0?"baseline":"comparison";
        if(!compare(model->forward(input),expected).at("passed").get<bool>())throw std::runtime_error("Sustained output failed");
        for(int i=0;i<warmups;++i)model->forward(input);
        scene(true,false); double begin=milliseconds();
        while(milliseconds()-begin<seconds*1000.){
          check(job,deadline); model->forward(input); auto frame=scene(false,false);conditions(scene);
          if(!frame.value("focused",false)||std::abs(frame.value("refresh_hz",0.)-72.)>.1||!frame.value("error",std::string{}).empty()){
            lost=true;throw std::runtime_error("XR focus or 72Hz refresh lost");}
        }
        report["arm_duration_seconds"][label]=(milliseconds()-begin)/1000.;
        auto frame=scene(false,true);
        report["frames"][label]=frame.at("frame_ms"); report["missed_frame_rate"][label]=frame.at("missed_frame_rate");
        if(!compare(model->forward(input),expected).at("passed").get<bool>())throw std::runtime_error("Sustained output failed after run");
      }
      report["duration_seconds"]=std::min(report["arm_duration_seconds"]["baseline"].get<double>(),report["arm_duration_seconds"]["comparison"].get<double>());
      report["refresh_hz"]=scene(false,false).at("refresh_hz");report["focus_lost"]=lost;
      observe(report,Json::parse(candidate->info()),shader_hashes);
    } else {
      std::vector<int> order(pairs); for(int i=0;i<pairs;++i)order[i]=i%2;
      std::mt19937 random(job.at("fixture_seed").get<uint32_t>()); std::shuffle(order.begin(),order.end(),random);
      for(int pair=0;pair<pairs;++pair){
        Json entry={{"index",pair},{"order",order[pair]==0?Json({"baseline","comparison"}):Json({"comparison","baseline"})}};
        for(int position=0;position<2;++position){
          int arm=position==0?order[pair]:1-order[pair];auto& model=arm==0?baseline:candidate;
          std::string label=arm==0?"baseline":"comparison";Json row={{"samples_ms",Json::array()}};
          check(job,deadline);row["conditions"]=conditions(scene);
          row["correctness_before"]=compare(model->forward(input),expected);
          if(!row["correctness_before"].at("passed").get<bool>())throw std::runtime_error("Candidate failed numerical validation");
          for(int i=0;i<warmups;++i){check(job,deadline);model->forward(input);}
          std::vector<xrgpu::Tensor> result;
          for(int i=0;i<count;++i){
            check(job,deadline);double start=milliseconds();
            if(stage=="slower"&&arm==1)for(int repeat=0;repeat<7;++repeat)model->forward(input);
            result=model->forward(input);row["samples_ms"].push_back(milliseconds()-start);
          }
          row["correctness_after"]=compare(result,expected);
          if(row["conditions"]!=conditions(scene))throw std::runtime_error("Thermal or charging state changed within timed block");
          entry[label]=row;
        }
        report["pairs"].push_back(entry);observe(report,Json::parse(candidate->info()),shader_hashes);save(output,report);
      }
    }
    report["status"]="complete";save(output,report);return 0;
  } catch(const std::exception& error){
    report["status"]="failed";report["error"]=error.what();
    if(!output.empty())try{save(output,report);}catch(...){}
    std::fprintf(stderr,"%s\n",error.what());return 1;
  }
}
}
