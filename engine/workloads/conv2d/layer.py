import numpy as np

from engine.workloads._shared import make_module


def make_weights(case, inputs, rand):
    p, x = case["params"], inputs[0]
    groups = p.get("groups", 1)
    shape = (p["out_channels"], x.shape[1] // groups, p["kernel"], p["kernel"])
    return {"weight": rand(shape, .1), "bias": rand((shape[0],))}


def reference(case, inputs, weights):
    p, x = case["params"], inputs[0]
    pad, stride, k = p.get("padding", 0), p.get("stride", 1), p["kernel"]
    padded = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    windows = np.lib.stride_tricks.sliding_window_view(
        padded, (k, k), axis=(2, 3)
    )[:, :, ::stride, ::stride]
    groups = p.get("groups", 1)
    cin, cout = x.shape[1] // groups, p["out_channels"] // groups
    result = np.concatenate([
        np.einsum("nchwkl,ockl->nohw", windows[:, g * cin:(g + 1) * cin],
                  weights["weight"][g * cout:(g + 1) * cout])
        for g in range(groups)
    ], axis=1)
    return result + weights["bias"][None, :, None, None]


def module(case, weights):
    import torch.nn.functional as F

    p = case.get("params", {})

    def forward(layer, x, *rest):
        return F.conv2d(x, layer.weight, layer.bias, stride=p.get("stride", 1),
                        padding=p.get("padding", 0), groups=p.get("groups", 1))

    return make_module(weights, forward)
