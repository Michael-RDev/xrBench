"""Build candidate shaders separately from the pinned stock shader library."""

import hashlib
import json
import subprocess
import runpy
from pathlib import Path
import yaml

from engine.paths import ROOT
from engine import kernel_package

SHADERS = ROOT / "artifacts/deps/executorch/backends/vulkan/runtime/graph/ops/glsl"


def configuration(package=None):
    package = kernel_package.resolve() if package is None else Path(package)
    config = json.loads((package / "matmul/config.json").read_text())
    if (
        set(config) != {"tile_m"}
        or type(config["tile_m"]) is not int
        or config["tile_m"] not in (1, 2, 4)
    ):
        raise ValueError("Matmul tile_m must be 1, 2 or 4")
    return config


def source_identity():
    return kernel_package.provenance()["content_sha256"]


def prepare_sources(destination, package=None):
    package = kernel_package.resolve() if package is None else Path(package)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    config = configuration(package)
    sources = {p.name: p for p in SHADERS.iterdir() if p.suffix in (".glslh", ".h")}
    sources.update(
        {
            p.name: p
            for p in (package / "matmul").iterdir()
            if p.suffix in (".glsl", ".yaml", ".glslh")
        }
    )
    for path in destination.iterdir():
        if path.is_file() and path.name not in sources:
            path.unlink()
    for name, path in sources.items():
        target = destination / name
        content = path.read_bytes()
        if path.suffix == ".yaml":
            template = yaml.safe_load(content)
            next(iter(template.values()))["parameter_names_with_default_values"]["TILE_M"] = config[
                "tile_m"
            ]
            content = yaml.safe_dump(template, sort_keys=False).encode()
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)

    # Give the intentionally wrong shader its own registered name and binary hash.
    for name in ('scalar','vec'):
        original='xrbench_matmul_'+name
        control='xrbench_incorrect_matmul_'+name
        (destination/(control+'.glsl')).write_bytes((package/'matmul'/(original+'.glsl')).read_bytes())
        template=yaml.safe_load((destination/(original+'.yaml')).read_text())
        spec=template[original];spec['parameter_names_with_default_values']['INCORRECT']=True
        for variant in spec['shader_variants']:variant['NAME']=variant['NAME'].replace('xrbench_','xrbench_incorrect_')
        (destination/(control+'.yaml')).write_text(yaml.safe_dump({control:spec},sort_keys=False))


def manifest(native, compiler, package=None):
    package = kernel_package.resolve() if package is None else Path(package)
    native = Path(native)
    entries = {}
    generator = runpy.run_path(
        str(ROOT / "artifacts/deps/executorch/backends/vulkan/runtime/gen_vulkan_spv.py")
    )
    environment = {
        **generator["DEFAULT_ENV"],
        **generator["TYPE_MAPPINGS"],
        **generator["UTILITY_FNS"],
    }
    for folder, variant in (
        (native / "vulkan_compute_shaders", "stock"),
        (native / "candidate_shaders", "candidate"),
    ):
        input_folder = SHADERS if variant == "stock" else native.parent / "candidate_sources"
        mapping = generator["SPVGenerator"](str(input_folder), environment, None).output_file_map
        for name, (template, parameters) in sorted(mapping.items()):
            if Path(template).suffix != ".glsl":
                continue
            path = folder / (name + ".spv")
            if not path.is_file():
                raise RuntimeError("Missing compiled shader: " + name)
            source = Path(template).relative_to(ROOT).as_posix()
            if variant == "candidate":
                source = kernel_package.source_url("matmul/" + (
                    "xrbench_matmul_vec.glsl"
                    if "matmul_vec" in name
                    else "xrbench_matmul_scalar.glsl"
                ))
            entries[name] = dict(
                variant=variant,
                validation_control="incorrect" if "incorrect" in name else "none",
                spirv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                source=source,
            )
    for tile in (1, 2):
        name = f'resident_matmul_tile{tile}'
        entries[name] = dict(variant='stock' if tile == 1 else 'candidate',
            source=kernel_package.source_url('resident/matmul.comp'), backend='vulkan_resident', tile_rows=tile,
            spirv_sha256=hashlib.sha256((native/(name+'.spv')).read_bytes()).hexdigest())
    if not any(e["variant"] == "candidate" for e in entries.values()):
        raise RuntimeError("Candidate shaders were not built")
    return dict(
        version=1,
        kernel_package=kernel_package.provenance(),
        candidate_revision=source_identity(),
        shaders=entries,
        compiler=subprocess.check_output([str(compiler), "--version"], text=True).strip(),
        flags=["-O", "--target-env=vulkan1.1"],
        candidate=dict(
            id="matmul_tile" + str(configuration(package)["tile_m"]),
            operations=["matmul"],
            tile_m=configuration(package)["tile_m"],
            status="experimental; performance must be measured on the target device",
        ),
    )
