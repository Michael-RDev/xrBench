import numpy as np

from layers._shared import make_module


def reference(case, inputs, weights):
    return np.maximum(inputs[0], 0)


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.relu(x)

    return make_module(weights, forward)
