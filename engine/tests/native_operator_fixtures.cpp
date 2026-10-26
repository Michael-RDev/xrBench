#include <xrgpu/operators.h>
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <stdexcept>
namespace op = xrgpu::operators;
using Json = nlohmann::json;
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::ifstream stream(argv[1]); Json suite; stream >> suite;
  for (const auto& entry : suite.at("cases")) {
    std::vector<op::Tensor> inputs, expected;
    for (const auto& category : {"inputs", "outputs"}) {
      auto& destination = std::string(category) == "inputs" ? inputs : expected;
      for (const auto& descriptor : entry.at(category)) {
        op::Tensor tensor; tensor.name = descriptor.at("name");
        tensor.shape = descriptor.at("shape").get<std::vector<int64_t>>();
        tensor.dtype = op::parse_dtype(descriptor.at("dtype"));
        destination.push_back(tensor);
      }
    }
    auto actual = op::plan(entry.at("operator"), inputs, expected);
    if (actual.size() != expected.size()) throw std::runtime_error("Fixture output count differs from native contract");
  }
  std::cout << "xrBench fixture descriptors match native operator API\n";
}
