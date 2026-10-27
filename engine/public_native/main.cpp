// Test adapter only; visitor execution is launched through the website and JNI.
#include "benchmark.h"
#include <fstream>
#include <iostream>
#include <cstdlib>
int main(int argc,char** argv){
    try{if(argc!=4)throw std::invalid_argument("Expected native suite, job JSON and evidence directory");
        std::ifstream in(argv[2]);nlohmann::json config;in>>config;
        // Deterministic fault injection exists only in this host test executable.
        auto control=config.value("test_control",nlohmann::json::object());
        bool deadline=false;
        xrbench::BenchmarkHooks hooks;
        if(control.value("action",std::string{})=="deadline")hooks.elapsed_ms=[&](){return deadline?1800001.:0.;};
        hooks.checkpoint=[&](const nlohmann::json& report){
            const auto& p=report.at("progress");
            if(!p.at("current_case").is_null()&&p.at("stage")==control.value("stage",std::string{})&&p.at("current_samples").get<int>()>=control.value("samples",0)){
                auto action=control.value("action",std::string{});
                if(action=="cancel"){std::ofstream marker(std::filesystem::path(argv[3])/"cancel");}
                if(action=="deadline")deadline=true;
                if(action=="crash")std::_Exit(23);
            }
        };
        auto report=xrbench::benchmark(argv[1],config,argv[3],{{"model","native software test"}},hooks);
        std::cout<<report.at("status").get<std::string>()<<'\n';
        if(report.at("status")!="complete")return 1;
        for(const auto& row:report.at("results"))if(row.at("status")!="passed")return 1;
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
