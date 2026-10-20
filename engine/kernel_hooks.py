"""Exact, reviewed integration hooks into the pinned Vulkan backend."""


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned Vulkan integration anchor changed: " + old[:70])
    return source.replace(old, new, 1)


def backend_source(source):
    source = ('#include "timing.h"\n' + source).replace(
        "config.enable_querypool = true;",
        "config.enable_querypool = xrbench::gpu_timestamps_supported;",
    )
    return replace_once(
        source,
        r"""", \"dispatch_id\": " + std::to_string(r.dispatch_id) + "}";""",
        r"""", \"dispatch_id\": " + std::to_string(r.dispatch_id) +
          ", \"global_size\": [" + std::to_string(r.metadata.global_workgroup_size[0]) + "," +
          std::to_string(r.metadata.global_workgroup_size[1]) + "," + std::to_string(r.metadata.global_workgroup_size[2]) +
          "], \"local_size\": [" + std::to_string(r.metadata.local_workgroup_size[0]) + "," +
          std::to_string(r.metadata.local_workgroup_size[1]) + "," + std::to_string(r.metadata.local_workgroup_size[2]) + "]}";""",
    )


def matmul_source(source):
    source = '#include "kernel_variant.h"\n' + source
    source = replace_once(
        source,
        "uint32_t pick_matmul_tile_m(ComputeGraph* graph, const ValueRef out) {",
        "uint32_t pick_matmul_tile_m(ComputeGraph* graph, const ValueRef out) {\n"
        "  if (xrbench::candidate_kernel) return xrbench::candidate_tile_m;",
    )
    source = replace_once(
        source,
        "  kernel_name.reserve(kShaderNameReserve);",
        "  if (xrbench::candidate_kernel) {\n"
        '    VK_CHECK_COND(!has_bias, "Candidate supports unbiased matmul only");\n'
        '    kernel_name = std::string(xrbench::incorrect_kernel ? "xrbench_incorrect_" : "xrbench_") + base;\n'
        "    ++xrbench::candidate_selections;\n"
        "  }\n  kernel_name.reserve(kShaderNameReserve);",
    )
    return replace_once(
        source,
        "const bool coopmat_eligible = is_coopmat_eligible(graph, out, M, N, K);",
        "const bool coopmat_eligible = !xrbench::candidate_kernel && is_coopmat_eligible(graph, out, M, N, K);",
    )
