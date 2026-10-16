import numpy as np

from layers._shared import make_module


def reference(case, inputs, weights):
    return np.tanh(inputs[0])


def module(case, weights):
    import torch

    def forward(layer, x, *rest):
        return torch.tanh(x)

    return make_module(weights, forward)
