import numpy as np

from engine.workloads._shared import make_module


def make_weights(case, inputs, rand):
    n = inputs[0].shape[1]
    return {
        "weight": (1 + rand((n,), .1)).astype(np.float32),
        "bias": rand((n,), .1),
        "running_mean": rand((n,)),
        "running_var": (1 + np.abs(rand((n,)))).astype(np.float32),
    }


def reference(case, inputs, weights):
    def channels(value):
        return value[None, :, None, None]

    return ((inputs[0] - channels(weights["running_mean"]))
            / np.sqrt(channels(weights["running_var"]) + 1e-5)
            * channels(weights["weight"]) + channels(weights["bias"]))


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.batch_norm(x, layer.running_mean, layer.running_var, layer.weight,
                            layer.bias, training=False, eps=1e-5)

    return make_module(weights, forward)
