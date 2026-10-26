import numpy as np

from engine.workloads._shared import make_module


def reference(case, inputs, weights):
    x = inputs[0]
    return x / (1 + np.exp(-x))


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.silu(x)

    return make_module(weights, forward)
