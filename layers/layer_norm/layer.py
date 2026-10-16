import numpy as np

from layers._shared import make_module


def make_weights(case, inputs, rand):
    n = inputs[0].shape[-1]
    return {"weight": (1 + rand((n,), .1)).astype(np.float32), "bias": rand((n,), .1)}


def reference(case, inputs, weights):
    x = inputs[0]
    centered = x - x.mean(axis=-1, keepdims=True)
    return (centered / np.sqrt((centered ** 2).mean(axis=-1, keepdims=True) + 1e-5)
            * weights["weight"] + weights["bias"])


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.layer_norm(x, (x.shape[-1],), layer.weight, layer.bias, 1e-5)

    return make_module(weights, forward)
