"""Synthetic residual MLP segment: projection, ReLU, projection, residual add."""

import numpy as np
from layers._shared import make_module


def make_weights(case, inputs, rand):
    width = inputs[0].shape[-1]
    hidden = case["params"]["hidden"]
    return dict(up=rand((width, hidden), 0.05), down=rand((hidden, width), 0.05))


def reference(case, inputs, weights):
    x = inputs[0]
    return np.maximum(x @ weights["up"], 0) @ weights["down"] + x


def module(case, weights):
    def forward(layer, x):
        return (x @ layer.up).relu() @ layer.down + x

    return make_module(weights, forward)
