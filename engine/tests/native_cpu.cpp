#include "kernels/cpu/matmul.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
#include <vector>
int main(){
  std::mt19937 rng(44);std::uniform_real_distribution<float>d(-1,1);
  for(int m:{1,3,17})for(int k:{1,7,32})for(int n:{1,3,4,5,19}){
    std::vector<float>a(m*k),b(k*n),scalar(m*n),neon(m*n);
    for(auto& v:a)v=d(rng);for(auto& v:b)v=d(rng);
    xrbench_cpu::matmul(a.data(),b.data(),scalar.data(),m,k,n,false);
    xrbench_cpu::matmul(a.data(),b.data(),neon.data(),m,k,n,true);
    for(int i=0;i<m;i++)for(int j=0;j<n;j++){
      double expected=0;for(int p=0;p<k;p++)expected+=double(a[i*k+p])*b[p*n+j];
      assert(std::abs(expected-scalar[i*n+j])<1e-4);
      assert(std::abs(expected-neon[i*n+j])<1e-4);
      assert(std::abs(expected-(neon[i*n+j]+100))>1);
    }
  }
  std::cout<<"CPU scalar and NEON correctness passed\n";
}
