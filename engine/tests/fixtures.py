import pytest


@pytest.fixture
def report():
    return dict(schema_version=2,run_id="run-"+"a"*32,status="complete",execution_location="headset",
        backend="executorch_vulkan",suite_revision="test-suite",runtime_revision="test-runtime",
        started_at="2026-10-16T00:00:00Z",finished_at="2026-10-16T00:00:01Z",device={"model":"synthetic test fixture"},
        configuration={"mode":"isolated","preset":"smoke","cases":["linear_odd"]},results=[dict(case="linear_odd",
            operation="linear",phase="isolated",trial=0,status="passed",correctness={"passed":True},
            samples=[dict(execution_ms=1.0,model_ms=.8,gpu_ms=.5)])])
