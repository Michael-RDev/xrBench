import shutil
import subprocess
from pathlib import Path

import pytest

from layers.engine.paths import ENGINE, LAYERS, ROOT

TESTS = Path(__file__).resolve().parent


def test_android_correctness_contract(tmp_path):
    base=ENGINE
    jar=ROOT/"artifacts/test-deps/json.jar"
    if not jar.exists() or not shutil.which("javac"):pytest.skip("Java and repository JSON test jar required")
    source=base/"android/app/src/main/java/org/xrgpu/benchmarks/Checks.java"
    subprocess.run(["javac","-cp",str(jar),"-d",str(tmp_path),str(source),str(TESTS/"NativeChecks.java")],check=True,capture_output=True)
    import os
    result=subprocess.run(["java","-cp",os.pathsep.join((str(jar),str(tmp_path))),"org.xrgpu.benchmarks.NativeChecks"],check=True,capture_output=True,text=True)
    assert "checks passed" in result.stdout


def test_native_tensor_validation(tmp_path):
    compiler=shutil.which("c++")
    if not compiler:pytest.skip("C++ compiler required")
    base=ENGINE
    executable=tmp_path/"tensor-checks"
    subprocess.run([compiler,"-std=c++17","-I",str(base/"native"),str(TESTS/"native_tensors.cpp"),"-o",str(executable)],check=True,capture_output=True)
    result=subprocess.run([str(executable)],check=True,capture_output=True,text=True)
    assert "native tensor checks passed" in result.stdout


def test_native_full_suite_scheduling(tmp_path):
    import os
    base=ENGINE;jar=ROOT/"artifacts/test-deps/json.jar"
    if not jar.exists() or not shutil.which("javac"):pytest.skip("Java and repository JSON test jar required")
    source=base/"android/app/src/main/java/org/xrgpu/benchmarks/SuitePlan.java"
    subprocess.run(["javac","-cp",str(jar),"-d",str(tmp_path),str(source),str(TESTS/"SuitePlanChecks.java")],check=True,capture_output=True)
    result=subprocess.run(["java","-cp",os.pathsep.join((str(jar),str(tmp_path))),"org.xrgpu.benchmarks.SuitePlanChecks",str(LAYERS/"catalog.json")],check=True,capture_output=True,text=True)
    assert "Full-suite scheduling checks passed" in result.stdout
