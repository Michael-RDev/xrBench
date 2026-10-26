# Native operator kernels

`python -m engine.operators` benchmarks the standalone xrGPU Vulkan operator
library on Android. Its typed tensor fixtures are separate from the existing
ExecuTorch FP32 layer suite. Kernel implementations stay in xrGPU; xrBench only
loads a verified local native package and supplies independent reference fixtures.

The initial fixture suite exercises `ai.onnx.Add` with broadcasting and scalars,
`ai.onnx.Relu` with signed inputs and zeros, and `ai.onnx.MatMul` with irregular,
vector and batched dimensions. It also covers empty outputs, zero inner products,
integer overflow, and the alpha library's declared dtype families. Floating
references use NumPy Float64 arithmetic rounded to Float32, with small exact
values for Float16 storage cases. Integer
and bool output comparisons preserve exact values; float comparisons use the
fixture's declared tolerance. This fixture suite does not establish conformance
for every operator, attribute or dtype in the upstream catalog.

Build a local native package in xrGPU first. The commands below require its
inventoried `include/xrgpu/operators.h`, Android arm64 shared library and shaders:

```sh
uv run python -m engine.operators prepare \
  --package ../xrGPU/artifacts/packages/native-operators-alpha-v4 \
  --output artifacts/operator-fixtures
uv run python -m engine.operators build \
  --package ../xrGPU/artifacts/packages/native-operators-alpha-v4 \
  --output artifacts/operator-runner
uv run python -m engine.operators run \
  --package ../xrGPU/artifacts/packages/native-operators-alpha-v4 \
  --suite artifacts/operator-fixtures \
  --runner artifacts/operator-runner \
  --output artifacts/operator-evidence \
  --execution-target headset
```

The build uses xrBench's pinned Android NDK and JSON headers. Specify `--serial`
when more than one device is connected. Native execution requires Android 10 or
newer, arm64-v8a, and the Vulkan capabilities required by the selected kernels.
No kernel download happens during execution. The legacy APK interface does not
expose this backend because it accepts only its original FP32 layer outputs.

Preparation, input upload and output readback are outside native timing samples.
The runner checks output correctness before and after measurement; xrBench then
checks the saved typed output bytes against its independent local references.
Unavailable GPU timestamps remain null. Reports record `backend=xrgpu_vulkan`,
package identity, native device information, output checks and native timing.
These are isolated operator observations, not model performance qualification or
OpenXR frame evidence. Thermal control and sustained XR qualification remain
separate work.

The default limit is 60 seconds. A run preserves its local `receipt.json` and
remote evidence directory even if transport or execution fails. Request
cooperative cancellation from another terminal with:

```sh
uv run python -m engine.operators cancel \
  --receipt artifacts/operator-evidence/receipt.json
```

The command writes a cancellation marker and retains partial samples. Existing
fixture and evidence directories are never overwritten; choose a new directory
for another run.
