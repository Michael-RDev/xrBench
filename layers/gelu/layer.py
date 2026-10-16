import math
import numpy as np

from layers._shared import make_module


def reference(case, inputs, weights):
    x = inputs[0]
    return .5 * x * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.gelu(x)

    return make_module(weights, forward)
