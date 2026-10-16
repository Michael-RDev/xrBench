import math

from layers._shared import make_module


def make_weights(case, inputs, rand):
    p, x = case.get("params", {}), inputs[0]
    weights = {"weight": rand((p["out_features"], x.shape[-1]), 1 / math.sqrt(x.shape[-1]))}
    if p.get("bias", True):
        weights["bias"] = rand((p["out_features"],))
    return weights


def reference(case, inputs, weights):
    return inputs[0] @ weights["weight"].T + weights.get("bias", 0)


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.linear(x, layer.weight, getattr(layer, "bias", None))

    return make_module(weights, forward)
