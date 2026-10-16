import math
import numpy as np

from benchmarks.layers._shared import make_module


def prepare_inputs(case, inputs):
    if len(inputs) == 4:
        inputs[3].fill(0)
        inputs[3][..., -inputs[3].shape[-1] // 3 :] = -10000


def reference(case, inputs, weights):
    x = inputs[0]
    scores = x @ inputs[1].swapaxes(-1, -2) / math.sqrt(x.shape[-1])
    scores = scores + (inputs[3] if len(inputs) == 4 else 0)
    e = np.exp(scores - scores.max(axis=-1, keepdims=True))
    return (e / e.sum(axis=-1, keepdims=True)) @ inputs[2]


def module(case, weights):
    import torch

    def forward(layer, x, *rest):
        scores = (x @ rest[0].transpose(-1, -2)) / math.sqrt(x.shape[-1])
        if len(rest) == 3:
            scores = scores + rest[2]
        return torch.softmax(scores, -1) @ rest[1]

    return make_module(weights, forward)
