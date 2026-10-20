# Third-party notices

The native benchmark links stock ExecuTorch (BSD-3-Clause), its Vulkan dependencies,
and Khronos OpenXR SDK (Apache-2.0 and component licenses). Revisions are pinned in
dependencies.json. The sole runtime patch gates timestamp queries on device support.
The benchmark does not include xrGPU's custom kernel dispatch or model weights.

Copies of upstream license texts are in licenses/ and bundled with the APK.
The Android NDK libc++ runtime includes the NDK notices and LLVM license exceptions.
Android tooling, Gradle, CMake, shaderc, Python packages, and JSON-java retain their
own licenses and are installed as local build/test dependencies.
