import json
import os
import subprocess

from engine.paths import ENGINE, ROOT
from engine.kernel_hooks import backend_source, matmul_source

SUBMODULES = [
    "backends/vulkan/third-party/Vulkan-Headers",
    "backends/vulkan/third-party/VulkanMemoryAllocator",
    "backends/vulkan/third-party/volk", "third-party/flatbuffers",
    "third-party/flatcc", "third-party/json", "third-party/pocketfft",
    "third-party/gflags", "backends/xnnpack/third-party/FXdiv",
    "backends/xnnpack/third-party/cpuinfo", "backends/xnnpack/third-party/pthreadpool",
    "backends/xnnpack/third-party/FP16", "backends/xnnpack/third-party/XNNPACK",
]


def windows_host_cmake(source):
    source = source.replace('if(WIN32)', 'if(CMAKE_HOST_WIN32)\n  set(_host_executable_suffix ".exe")')
    for name in ("flatc", "flatcc"):
        source = source.replace(f'BUILD_BYPRODUCTS <INSTALL_DIR>/bin/{name}\n',
                                f'BUILD_BYPRODUCTS <INSTALL_DIR>/bin/{name}${{_host_executable_suffix}}\n')
    source = source.replace('if(CMAKE_CROSSCOMPILING AND ${CMAKE_SYSTEM_PROCESSOR} MATCHES "ARM64|arm64|aarch64")',
                            'if(CMAKE_GENERATOR MATCHES "Visual Studio" AND CMAKE_CROSSCOMPILING AND ${CMAKE_SYSTEM_PROCESSOR} MATCHES "ARM64|arm64|aarch64")')
    return source.replace('set(_executorch_external_project_additional_args)\nelse()',
                          'if("$ENV{XRBENCH_HOST_GENERATOR}" STREQUAL "Ninja")\n'
                          '    set(_executorch_external_project_additional_args CMAKE_GENERATOR "Ninja" CMAKE_CACHE_ARGS "-DCMAKE_BUILD_TYPE:STRING=Release")\n'
                          '  else()\n'
                          '    set(_executorch_external_project_additional_args CMAKE_GENERATOR "Visual Studio 17 2022" CMAKE_GENERATOR_PLATFORM x64)\n'
                          '  endif()\nelse()')


def shader_newlines(source):
    # codecs.open preserves CRLF, which otherwise defeats the macro-continuation regex.
    return source.replace('    # Workaround to handle source files using',
                          '    input_text = input_text.replace("\\r\\n", "\\n")\n    # Workaround to handle source files using')


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def verify():
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for name in ("executorch", "openxr"):
        source = ROOT / "artifacts/deps" / name
        if git("rev-parse", "HEAD", cwd=source).stdout.strip() != pins[name]["commit"]:
            raise RuntimeError(f"Pinned dependency mismatch: {name}")
        changed = set(git("diff", "HEAD", "--name-only", cwd=source).stdout.splitlines())
        expected = {"backends/vulkan/runtime/VulkanBackend.cpp", "backends/vulkan/runtime/graph/ops/impl/Matmul.cpp", "third-party/CMakeLists.txt", "backends/vulkan/runtime/gen_vulkan_spv.py"} if name == "executorch" else set()
        if changed != expected:
            raise RuntimeError(f"Unexpected dependency modifications: {name}: {sorted(changed)}")
        if name == "executorch":
            patch = ENGINE / "patches/0001-capability-timestamps.patch"
            git("apply", "--reverse", "--check", str(patch), cwd=source)
            backend = "backends/vulkan/runtime/VulkanBackend.cpp"
            original = git("show", f"HEAD:{backend}", cwd=source).stdout
            expected_text = backend_source(original)
            if (source / backend).read_text() != expected_text:
                raise RuntimeError("Vulkan backend differs from the declared timestamp/dispatch hooks")
            matmul = "backends/vulkan/runtime/graph/ops/impl/Matmul.cpp"
            if (source / matmul).read_text() != matmul_source(git("show", f"HEAD:{matmul}", cwd=source).stdout):
                raise RuntimeError("Matmul differs from the declared candidate selection hook")
            cmake = "third-party/CMakeLists.txt"
            if (source / cmake).read_text() != windows_host_cmake(git("show", f"HEAD:{cmake}", cwd=source).stdout):
                raise RuntimeError("Dependency CMake has changes beyond Windows host tool selection")
            generator = "backends/vulkan/runtime/gen_vulkan_spv.py"
            if (source / generator).read_text() != shader_newlines(git("show", f"HEAD:{generator}", cwd=source).stdout):
                raise RuntimeError("Shader generator has changes beyond CRLF normalization")
            extras = git("ls-files", "--others", "--exclude-standard", cwd=source).stdout.splitlines()
            if any(p.endswith((".glsl", ".yaml", ".cpp", ".h")) for p in extras):
                raise RuntimeError("Put candidate sources in kernels/, not the pinned dependency checkout")


def prepare():
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for name in ("executorch", "openxr"):
        spec = pins[name]
        dest = ROOT / "artifacts/deps" / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--depth", "1", "--branch", spec["ref"], spec["url"], str(dest), cwd=ROOT)
        if git("rev-parse", "HEAD", cwd=dest).stdout.strip() != spec["commit"]:
            raise RuntimeError(f"Unexpected {name} revision; refusing to reset")
        if name == "executorch":
            git("submodule", "update", "--init", "--depth", "1", *SUBMODULES, cwd=dest)
    source = ROOT / "artifacts/deps/executorch"
    patch = ENGINE / "patches/0001-capability-timestamps.patch"
    if git("apply", "--reverse", "--check", str(patch), cwd=source, check=False).returncode:
        git("apply", "--check", str(patch), cwd=source)
        git("apply", str(patch), cwd=source)
    cmake = source / "third-party/CMakeLists.txt"
    original = git("show", "HEAD:third-party/CMakeLists.txt", cwd=source).stdout
    expected = windows_host_cmake(original)
    # Accept only the previous generated host-tool patch when upgrading it.
    previous_ninja = expected.replace(' CMAKE_CACHE_ARGS "-DCMAKE_BUILD_TYPE:STRING=Release"', '')
    previous = previous_ninja.replace(
        'if("$ENV{XRBENCH_HOST_GENERATOR}" STREQUAL "Ninja")\n'
        '    set(_executorch_external_project_additional_args CMAKE_GENERATOR "Ninja")\n'
        '  else()\n'
        '    set(_executorch_external_project_additional_args CMAKE_GENERATOR "Visual Studio 17 2022" CMAKE_GENERATOR_PLATFORM x64)\n'
        '  endif()',
        'set(_executorch_external_project_additional_args CMAKE_GENERATOR "Visual Studio 17 2022" CMAKE_GENERATOR_PLATFORM x64)')
    if cmake.read_text() in (original, previous, previous_ninja):
        cmake.write_text(expected)
    elif cmake.read_text() != expected:
        raise RuntimeError("Unexpected dependency CMake changes; refusing to overwrite")
    generator = source / "backends/vulkan/runtime/gen_vulkan_spv.py"
    original = git("show", "HEAD:backends/vulkan/runtime/gen_vulkan_spv.py", cwd=source).stdout
    if generator.read_text() == original:
        generator.write_text(shader_newlines(original))
    elif generator.read_text() != shader_newlines(original):
        raise RuntimeError("Unexpected shader generator changes; refusing to overwrite")
    install_kernel_hooks()
    verify()
    if os.name == "nt":
        build = ROOT / "artifacts/toolchain/flatbuffers-build"
        subprocess.run(["cmake", "-S", str(source / "third-party/flatbuffers"), "-B", str(build),
                        "-G", "Visual Studio 17 2022", "-A", "x64", "-DFLATBUFFERS_BUILD_TESTS=OFF",
                        "-DFLATBUFFERS_BUILD_FLATLIB=OFF"], check=True)
        subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--target", "flatc", "-j", "4"], check=True)
    print("Pinned stock native dependencies ready.")


def install_kernel_hooks():
    source = ROOT / "artifacts/deps/executorch"
    for relative, transform in (("backends/vulkan/runtime/VulkanBackend.cpp", backend_source),
                                ("backends/vulkan/runtime/graph/ops/impl/Matmul.cpp", matmul_source)):
        path = source / relative
        original = git("show", f"HEAD:{relative}", cwd=source).stdout
        previous = ('#include "timing.h"\n' + original).replace("config.enable_querypool = true;", "config.enable_querypool = xrbench::gpu_timestamps_supported;") if relative.endswith("VulkanBackend.cpp") else original
        expected = transform(original)
        legacy = expected.replace('kernel_name = std::string(xrbench::incorrect_kernel ? "xrbench_incorrect_" : "xrbench_") + base;', 'kernel_name = "xrbench_" + base;')
        if path.read_text() not in (original, previous, expected, legacy):
            raise RuntimeError("Refusing to overwrite unrecognized dependency edits: " + relative)
        if path.read_text() != expected:
            path.write_text(expected)


if __name__ == "__main__":
    prepare()
