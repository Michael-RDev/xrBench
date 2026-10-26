// Test adapter only; visitor execution is launched through the website and JNI.
#include "benchmark.h"
#include <fstream>
#include <iostream>
int main(int argc,char** argv){
    try{if(argc!=4)throw std::invalid_argument("Expected native suite, job JSON and evidence directory");
        std::ifstream in(argv[2]);nlohmann::json config;in>>config;
        auto report=xrbench::benchmark(argv[1],config,argv[3],{{"model","native software test"}});
        std::cout<<report.at("status").get<std::string>()<<'\n';
        if(report.at("status")!="complete")return 1;
        for(const auto& row:report.at("results"))if(row.at("status")!="passed")return 1;
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
