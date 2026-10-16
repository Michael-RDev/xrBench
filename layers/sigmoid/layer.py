import numpy as np

from layers._shared import make_module


def reference(case, inputs, weights):
    return 1 / (1 + np.exp(-inputs[0]))


def module(case, weights):
    import torch

    def forward(layer, x, *rest):
        return torch.sigmoid(x)

    return make_module(weights, forward)
