#include "runner.h"
#include <cstdio>
int main(int argc,char** argv){
  if(argc!=2){std::fprintf(stderr,"Usage: xrgpu-qualification-runner JOB.json\n");return 2;}
  return xrbench_qualification::execute_file(argv[1]);
}
