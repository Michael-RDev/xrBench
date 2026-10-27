// SPDX-License-Identifier: Apache-2.0
#include "benchmark.h"
#include <xrgpu/operators.h>
#include <xrgpu/extended_operators.h>
#include "sha256.h"
#include <chrono>
#include <cmath>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <memory>
#include <limits>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace xrbench {
using Json=nlohmann::json;
namespace fs=std::filesystem;
namespace op=xrgpu::operators;
namespace ex=op::extended;
using Clock=std::chrono::steady_clock;
struct RunStopped : std::runtime_error {using std::runtime_error::runtime_error;};
static double elapsed(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
static std::string timestamp(){
    auto instant=std::chrono::system_clock::now();
    auto now=std::chrono::system_clock::to_time_t(instant);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc,&now);
#else
    gmtime_r(&now,&utc);
#endif
    auto millis=std::chrono::duration_cast<std::chrono::milliseconds>(instant.time_since_epoch()).count()%1000;
    std::ostringstream text;text<<std::put_time(&utc,"%Y-%m-%dT%H:%M:%S")<<'.'<<std::setfill('0')<<std::setw(3)<<millis<<'Z';return text.str();
}
static fs::path contained(const fs::path& root,const std::string& name){
    if(name.empty()||fs::path(name).is_absolute()||name.find('\\')!=std::string::npos)throw std::invalid_argument("Invalid native asset path");
    fs::path result=root;
    for(const auto& part:fs::path(name)){
        if(part.empty()||part=="."||part=="..")throw std::invalid_argument("Native asset path escape");
        result/=part;if(fs::is_symlink(result))throw std::invalid_argument("Native asset links are forbidden");
    }
    return result;
}
static Json read_json(const fs::path& file){std::ifstream in(file);Json value;in>>value;if(!in)throw std::runtime_error("Cannot read native benchmark JSON");return value;}
static void save(const fs::path& file,const Json& value){
    auto part=file.string()+".part";{std::ofstream out(part);out<<value.dump()<<'\n';if(!out)throw std::runtime_error("Cannot preserve native evidence");}
    fs::rename(part,file);
}
static std::vector<uint8_t> bytes(const fs::path& file){
    std::ifstream in(file,std::ios::binary|std::ios::ate);auto size=in.tellg();
    if(!in||size<0||size>64*1024*1024)throw std::invalid_argument("Invalid native tensor file");
    std::vector<uint8_t> data(static_cast<size_t>(size));in.seekg(0);if(size)in.read(reinterpret_cast<char*>(data.data()),size);
    if(!in)throw std::runtime_error("Incomplete native tensor");return data;
}
static op::Tensor tensor(const fs::path& root,const Json& spec,bool payload){
    op::Tensor t;t.name=spec.at("name");t.shape=spec.at("shape").get<std::vector<int64_t>>();
    t.dtype=op::parse_dtype(spec.at("dtype"));t.storage_dtype=op::parse_dtype(spec.value("storage_dtype",spec.at("dtype").get<std::string>()));
    if(payload){auto file=contained(root,spec.at("file"));if(xrgpu::file_sha256(file.string())!=spec.at("sha256"))throw std::runtime_error("Native fixture checksum mismatch");t.data=bytes(file);}
    return t;
}
static ex::Attributes metadata(const fs::path& root,const Json& source){
    ex::Attributes result;
    for(auto it=source.begin();it!=source.end();++it){const auto& v=it.value();auto key=it.key();
        if(v.is_boolean())result.integers[key]=v.get<bool>();
        else if(v.is_number_integer()||v.is_number_unsigned())result.integers[key]=v.get<int64_t>();
        else if(v.is_number_float())result.numbers[key]=v.get<double>();
        else if(v.is_string())result.strings[key]=v.get<std::string>();
        else if(v.is_array()){
            bool integers=true,strings=!v.empty(),numbers=true;
            for(const auto& item:v){integers&=item.is_number_integer()||item.is_number_unsigned()||item.is_boolean();strings&=item.is_string();numbers&=item.is_number()||item.is_boolean();}
            if(integers){for(const auto& item:v)result.integer_arrays[key].push_back(item.is_boolean()?int(item.get<bool>()):item.get<int64_t>());if(v.empty()){result.integer_arrays[key]={};result.number_arrays[key]={};result.string_arrays[key]={};}}
            else if(numbers)result.number_arrays[key]=v.get<std::vector<double>>();
            else if(strings)result.string_arrays[key]=v.get<std::vector<std::string>>();
            else throw std::invalid_argument("Mixed native attribute array");
        }else if(v.is_object()&&v.contains("dtype")&&v.contains("shape"))result.tensors[key]=tensor(root,v,true);
        else throw std::invalid_argument("Unsupported native attribute encoding");
    }
    return result;
}
static op::DType storage(const op::Tensor& t){return t.storage_dtype==op::DType::Unspecified?t.dtype:t.storage_dtype;}
static double floating(const uint8_t* p,op::DType dtype){
    if(dtype==op::DType::Float32){float value;std::memcpy(&value,p,4);return value;}
    uint16_t bits;std::memcpy(&bits,p,2);double sign=(bits&0x8000)?-1.:1.;unsigned exponent=(bits>>10)&31,mantissa=bits&1023;
    if(exponent==31)return mantissa?std::numeric_limits<double>::quiet_NaN():sign*std::numeric_limits<double>::infinity();
    return sign*std::ldexp(exponent?1.+mantissa/1024.:mantissa/1024.,exponent?int(exponent)-15:-14);
}
static Json compare(const fs::path& root,const std::vector<op::Tensor>& actual,const Json& expected){
    bool passed=actual.size()==expected.size();Json outputs=Json::array();
    for(size_t i=0;i<actual.size()&&i<expected.size();++i){const auto& a=actual[i];const auto& e=expected[i];auto b=tensor(root,e,true);
        bool good=a.name==b.name&&a.shape==b.shape&&a.dtype==b.dtype&&storage(a)==storage(b)&&a.data.size()==b.data.size();double max_error=0;
        bool fp=storage(a)==op::DType::Float32||storage(a)==op::DType::Float16;
        if(good&&fp){auto rules=e.at("comparison");double atol=rules.at("atol"),rtol=rules.at("rtol");bool nan=rules.value("equal_nan",false);auto width=op::dtype_bytes(storage(a));
            for(size_t offset=0;offset<a.data.size();offset+=width){double x=floating(a.data.data()+offset,storage(a)),y=floating(b.data.data()+offset,storage(b));
                if(std::isfinite(x)&&std::isfinite(y)){auto error=std::abs(x-y);max_error=std::max(max_error,error);good&=error<=atol+rtol*std::abs(y);}
                else good&=x==y||(nan&&std::isnan(x)&&std::isnan(y));}
        }else if(good)good=a.data==b.data;
        outputs.push_back({{"name",a.name},{"passed",good},{"comparison",fp?"absolute_relative":"exact"},{"max_abs_error",max_error}});passed&=good;
    }
    return {{"passed",passed},{"outputs",outputs}};
}
Json probe(){return Json::parse(op::probe());}

Json benchmark(const fs::path& root,const Json& config,const fs::path& output,const Json& device,const BenchmarkHooks& hooks){
    std::string id=config.at("run_id");if(!std::regex_match(id,std::regex("run-[a-f0-9]{32}")))throw std::invalid_argument("Invalid native run identity");
    auto suite=read_json(root/"suite.json");
    if(suite.at("operator_count")!=221||suite.at("cases").size()!=221||config.at("suite_revision")!=suite.at("suite_revision")||config.at("backend")!="xrgpu_vulkan"||config.at("mode")!="isolated")throw std::invalid_argument("Native suite/configuration mismatch");
    if(fs::exists(output/"report.json"))throw std::invalid_argument("Recover existing native evidence instead of replaying it");
    int warmups=config.value("warmups",20),samples=config.value("samples",100);
    if(warmups<0||warmups>100||samples<1||samples>1000)throw std::invalid_argument("Invalid native sample limits");
    fs::create_directories(output);
    auto started=Clock::now(),last_snapshot=started-std::chrono::seconds(2);int sequence=0,completed=0;
    Json report={{"schema_version",3},{"run_id",id},{"backend","xrgpu_vulkan"},{"status","running"},
        {"configuration",config},{"suite_revision",suite.at("suite_revision")},{"runtime_revision",config.at("runtime_revision")},
        {"started_at",timestamp()},{"execution_location",config.at("execution_target")},{"processor","gpu"},{"device",device},
        {"native_package_sha256",suite.at("source_package_sha256")},{"native_catalog_sha256",suite.at("native_catalog_sha256")},
        {"execution_context","foreground_service"},{"results",Json::array()},{"shaders",Json::object()},{"stage_events",Json::array()},
        {"timing_scope","execution_ms: native Vulkan run (host staging, encode, submit, wait); gpu_ms sums GPU timestamp intervals. prepare_ms includes initial input transfer; readback is separate. No CPU numerical fallback."}};
#ifdef XRBENCH_SOFTWARE_TEST
    report["test_only"]=true;report["physical_qualification"]=false;
#endif
    auto canceled=[&](){return fs::exists(output/"cancel");};
    auto environment=[&](){return fs::exists(output/"environment.json")?read_json(output/"environment.json"):Json::object();};
    auto duration=[&](){
#ifdef XRBENCH_SOFTWARE_TEST
        if(hooks.elapsed_ms)return hooks.elapsed_ms();
#endif
        return elapsed(started);
    };
    auto check=[&](){if(canceled())throw RunStopped(fs::exists(output/"cancel-reason.json")?read_json(output/"cancel-reason.json").value("reason",std::string("Canceled by user")):"Canceled by user");if(duration()>1800000)throw RunStopped("Native benchmark deadline exceeded");auto state=environment();if(state.contains("stop_reason"))throw RunStopped(state.at("stop_reason").get<std::string>());};
    std::string stage="preparation";
    auto persist=[&](const std::string& current,int count,bool force){
        Json progress={{"run_id",id},{"status",report["status"]},{"sequence",++sequence},{"completed_cases",completed},
            {"total_cases",221},{"current_case",current.empty()?Json(nullptr):Json(current)},{"current_samples",count},
            {"target_samples",samples},{"processor","gpu"},{"phase",current.empty()?(report["status"]=="running"?"preparation":"finished"):"execution"},
            {"stage",stage},{"elapsed_ms",elapsed(started)},{"updated_at",timestamp()},{"message",current.empty()?(report["status"]=="running"?"Preparing native operators":"Native benchmark finished"):stage+": "+current}};
        report["progress"]=progress;
        if(force||elapsed(last_snapshot)>=1000){save(output/"report.json",report);last_snapshot=Clock::now();}
        save(output/"progress.json",progress);
        if(hooks.checkpoint)hooks.checkpoint(report);
    };
    auto transition=[&](const std::string& next,const std::string& current,int count){
        stage=next;
        report["stage_events"].push_back({{"stage",stage},{"case",current.empty()?Json(nullptr):Json(current)},
            {"at",timestamp()},{"elapsed_ms",elapsed(started)},{"environment",environment()}});
        // Stage markers and checkpoint I/O never occur inside a timed native call.
        persist(current,count,true);
    };
    transition("preparation","",0);
    try{
        for(const auto& spec:suite.at("cases")){
            check();std::string operation=spec.at("id");
            Json initial={{"case",operation},{"operation",operation},{"phase","isolated"},{"trial",0},{"status","interrupted"},
                {"error","Native work has not completed"},
                {"inputs",spec.at("inputs")},{"outputs",spec.at("outputs")},{"attrs",spec.at("attrs")},{"args",spec.at("args")},
                {"contract_revision",spec.at("contract_revision")},{"contract_sha256",spec.at("contract_sha256")},
                {"samples",Json::array()},{"shaders",Json::array()}};
            report["results"].push_back(initial);auto& row=report["results"].back();
            auto state=environment();row["thermal_start"]=state.value("thermal_status",Json(nullptr));row["charging_start"]=state.value("charging",Json(nullptr));row["display_interactive_start"]=state.value("display_interactive",Json(nullptr));
            transition("preparation",operation,0);
            std::unique_ptr<op::Operator> legacy;std::unique_ptr<ex::Operator> extended;
            try{
                auto fixture_start=Clock::now();
                std::vector<op::Tensor> inputs,wanted;for(const auto& value:spec.at("inputs"))inputs.push_back(tensor(root,value,true));for(const auto& value:spec.at("outputs"))wanted.push_back(tensor(root,value,false));
                row["fixture_load_ms"]=elapsed(fixture_start);
                row["input_transfer_ms"]=nullptr;row["input_transfer_scope"]="Included in prepare_ms by the xrGPU prepare API";
                op::Options options;options.profiling=true;
                auto prepare_start=Clock::now();
                if(spec.at("native_api")=="legacy")legacy=op::Operator::prepare((root/"runtime").string(),operation,inputs,options,wanted);
                else {ex::Request request;request.operation=operation;request.inputs=inputs;request.requested_outputs=wanted;request.attrs=metadata(root,spec.at("attrs"));request.args=metadata(root,spec.at("args"));extended=ex::Operator::prepare((root/"runtime").string(),request,options);}
                row["prepare_ms"]=elapsed(prepare_start);
                auto run=[&](){if(legacy)legacy->run();else extended->run();};
                auto info=[&](){return Json::parse(legacy?legacy->info():extended->info());};
                auto read=[&](){return legacy?legacy->read_outputs():extended->read_outputs();};
                check();run();auto first=info();
#ifdef XRBENCH_SOFTWARE_TEST
                if(first.value("backend",std::string{})!="software_vulkan"||first.value("cpu_compute_fallback",true))throw std::runtime_error("Explicit native software Vulkan test runtime required");
#else
                if(first.value("backend",std::string{})!="vulkan"||first.value("test_only",false)||first.value("cpu_compute_fallback",true))throw std::runtime_error("Physical native Vulkan required; CPU fallback is prohibited");
#endif
                row["runtime_info"]=first;
                Json executed_shaders=legacy?Json::array({Json{{"name",first.at("shader")},{"sha256",first.at("shader_sha256")}}}):first.at("shaders");
                for(const auto& shader:executed_shaders){
                    std::string name=shader.at("name");if(name.size()<4||name.substr(name.size()-4)!=".spv")name+=".spv";
                    std::string file="shaders/"+std::string(legacy?"":"extended/")+name;
                    if(!suite.at("runtime_files").contains(file)||suite.at("runtime_files").at(file)!=shader.at("sha256"))throw std::runtime_error("Executed shader differs from verified release");
                    row["shaders"].push_back({{"file",file},{"sha256",shader.at("sha256")}});report["shaders"][file]=shader.at("sha256");
                }
                transition("readback",operation,0);
                auto read_start=Clock::now();auto before=read();row["readback_before_ms"]=elapsed(read_start);row["correctness_before"]=compare(root,before,spec.at("outputs"));
                if(!row["correctness_before"].at("passed").get<bool>())throw std::runtime_error("Native output differs from independent reference before timing");
                transition("warmup",operation,0);
                row["warmup_execution_ms"]=0.;
                for(int i=0;i<warmups;++i){check();auto warmup_start=Clock::now();run();row["warmup_execution_ms"]=row["warmup_execution_ms"].get<double>()+elapsed(warmup_start);}
                transition("execution",operation,0);
                for(int i=0;i<samples;++i){check();auto sample_start=Clock::now();run();double ms=elapsed(sample_start);auto timing=info();
                    auto health=environment();
                    row["samples"].push_back({{"execution_ms",ms},{"gpu_ms",timing.value("gpu_ms",Json(nullptr))},
                        {"at",timestamp()},{"elapsed_ms",elapsed(started)},
                        {"thermal_status",health.value("thermal_status",Json(nullptr))},{"charging",health.value("charging",Json(nullptr))},
                        {"display_interactive",health.value("display_interactive",Json(nullptr))}});
                    if(i%10==0)persist(operation,i+1,i==0);
                }
                check();transition("readback",operation,int(row["samples"].size()));
                read_start=Clock::now();auto after=read();row["readback_after_ms"]=elapsed(read_start);row["correctness_after"]=compare(root,after,spec.at("outputs"));row["correctness"]=row["correctness_after"];
                row["actual_outputs"]=Json::array();auto case_dir=output/operation;fs::create_directories(case_dir);
                for(size_t i=0;i<after.size();++i){auto file=case_dir/("output-"+std::to_string(i)+".bin");{std::ofstream out(file,std::ios::binary);if(!after[i].data.empty())out.write(reinterpret_cast<const char*>(after[i].data.data()),after[i].data.size());if(!out)throw std::runtime_error("Cannot preserve typed native output");}
                    row["actual_outputs"].push_back({{"name",after[i].name},{"shape",after[i].shape},{"dtype",op::dtype_name(after[i].dtype)},{"storage_dtype",op::dtype_name(storage(after[i]))},{"sha256",xrgpu::file_sha256(file.string())}});}
                row["status"]=row["correctness_after"].at("passed").get<bool>()?"passed":"failed";
                if(row["status"]=="failed")row["error"]="Native output differs from independent reference after timing";
                else row.erase("error");
            }catch(const std::exception& error){
                row["error"]=error.what();row["status"]="failed";std::string reason=error.what();
                if(canceled()||dynamic_cast<const RunStopped*>(&error))row["status"]="interrupted";
                else if(reason.find("unsupported")!=std::string::npos||reason.find("Unsupported")!=std::string::npos||reason.find("Vulkan 1.1")!=std::string::npos||reason.find("GPU memory")!=std::string::npos||reason.find("device storage limit")!=std::string::npos||reason.find("Vulkan compute limits")!=std::string::npos||reason.find("dispatch dimensions")!=std::string::npos||reason.find("Host-visible Vulkan heap")!=std::string::npos||reason.find("allocation limit exceeded")!=std::string::npos||reason.find("Operator memory limit")!=std::string::npos)row["status"]="unsupported";
            }
            if(legacy)legacy->close();if(extended)extended->close();
            if(row["status"]!="interrupted")++completed;
            persist(operation,int(row["samples"].size()),true);check();
        }
        report["status"]="complete";
    }catch(const std::exception& error){report["status"]=canceled()?"canceled":"failed";report["error"]=error.what();}
    report["finished_at"]=timestamp();transition("finished","",0);return report;
}
}
