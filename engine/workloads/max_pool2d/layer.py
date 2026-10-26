import numpy as np

from engine.workloads._shared import make_module


def reference(case, inputs, weights):
    p = case["params"]
    windows = np.lib.stride_tricks.sliding_window_view(
        inputs[0], (p["kernel"], p["kernel"]), axis=(2, 3)
    )[:, :, ::p["stride"], ::p["stride"]]
    return windows.max(axis=(-2, -1))


def module(case, weights):
    import torch.nn.functional as F

    p = case.get("params", {})

    def forward(layer, x, *rest):
        return F.max_pool2d(x, p["kernel"], p["stride"])

    return make_module(weights, forward)
