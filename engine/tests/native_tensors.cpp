#include "tensor_validation.h"
#include <cassert>
#include <iostream>

int main() {
  using xrgpu_benchmarks::validate_tensor;
  assert(validate_tensor({2,3,5},"float32",120)==30);
  assert(validate_tensor({1,128},"int64",1024)==128);
  auto rejects=[](auto shape,auto type,size_t bytes){
    try{validate_tensor(shape,type,bytes);return false;}catch(const std::runtime_error&){return true;}
  };
  using Shape=std::vector<int32_t>;
  assert(rejects(Shape{},"float32",0));
  assert(rejects(Shape{0,3},"float32",0));
  assert(rejects(Shape{-1,3},"float32",12));
  assert(rejects(Shape{2147483647,2147483647},"float32",0));
  assert(rejects(Shape(9,1),"float32",4));
  assert(rejects(Shape{1},"float16",2));
  assert(rejects(Shape{2},"int64",8));
  std::cout<<"native tensor checks passed\n";
}
