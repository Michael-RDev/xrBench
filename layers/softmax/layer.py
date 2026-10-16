import numpy as np

from layers._shared import make_module


def reference(case, inputs, weights):
    x = inputs[0]
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def module(case, weights):
    import torch

    def forward(layer, x, *rest):
        return torch.softmax(x, -1)

    return make_module(weights, forward)
