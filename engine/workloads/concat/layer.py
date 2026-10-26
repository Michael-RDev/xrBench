import numpy as np

from engine.workloads._shared import make_module


def reference(case, inputs, weights):
    return np.concatenate(inputs, axis=case["params"]["dim"])


def module(case, weights):
    import torch

    p = case.get("params", {})

    def forward(layer, x, *rest):
        return torch.cat((x, *rest), dim=p["dim"])

    return make_module(weights, forward)
