import numpy as np

from benchmarks.layers._shared import make_module


def make_weights(case, inputs, rand):
    n = inputs[0].shape[-1]
    return {"weight": (1 + rand((n,), .1)).astype(np.float32)}


def reference(case, inputs, weights):
    x = inputs[0]
    return x / np.sqrt((x ** 2).mean(axis=-1, keepdims=True) + 1e-5) * weights["weight"]


def module(case, weights):
    import torch

    def forward(layer, x, *rest):
        return x * torch.rsqrt((x * x).mean(-1, keepdim=True) + 1e-5) * layer.weight

    return make_module(weights, forward)
