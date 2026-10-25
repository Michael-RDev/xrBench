# Third-party notices

The native benchmark links ExecuTorch (BSD-3-Clause), its portable CPU kernels and
Vulkan dependencies, and Khronos OpenXR SDK (Apache-2.0 and component licenses).
Revisions are pinned in dependencies.json. The checked integration hooks add
capability-gated timestamps, dispatch metadata, and benchmark candidate selection.
CPU/GPU matmul and calibration implementations are fetched from the pinned xrGPU
benchmark source package and compiled into the APK. The candidate GLSL templates
retain Meta's copyright and BSD-3-Clause license; the package license is also
bundled as `licenses/xrgpu-benchmark-LICENSE.txt`.

The benchmark does not bundle xrGPU's model weights, tokenizer assets, XNNPACK
delegate, KleidiAI kernels, or separate Quest 3 package. Model inference components
and their notices belong to xrGPU's demo. ExecuTorch support libraries such as
cpuinfo, pthreadpool, and FXdiv remain separate dependencies even though the
XNNPACK delegate is disabled.

Copies of upstream license texts are in licenses/ and bundled with the APK.
The Android NDK libc++ runtime includes the NDK notices and LLVM license exceptions.
Android tooling, Gradle, CMake, shaderc, Python packages, and JSON-java retain their
own licenses and are installed as local build/test dependencies.

The hosted homepage bundles Tango ADB (`@yume-chan/adb`, its WebUSB transport and
browser credential store) and their dependencies. Versions and integrity hashes
are pinned in package-lock.json. The static distribution includes license texts
and package metadata for the bundled libraries under licenses/; Tango ADB is MIT
licensed. esbuild, jsdom and fake-indexeddb are build/test dependencies only.
