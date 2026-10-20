#include "gpu_span.h"
#include <cassert>
#include <iostream>
int main(){
  xrgpu::GpuSpan span;assert(!span.finish(1,false));
  span.add(1000000,2000000);span.add(1500000,3000000);
  assert(*span.finish(1,false)==2); // Interval sums would double-count the overlap.
  assert(*span.finish(1,false,1.5)==3);
  assert(!span.finish(2,false)&&!span.finish(0,false)&&!span.finish(1,true));
  assert(!span.finish(1,false,0));
  xrgpu::GpuSpan reversed;reversed.add(10,5);assert(!reversed.finish(1,false));
  xrgpu::GpuSpan wrapped;wrapped.add(1000,2000);wrapped.add(10,20);assert(!wrapped.finish(1,false));
  std::cout<<"GPU span checks passed\n";
}
