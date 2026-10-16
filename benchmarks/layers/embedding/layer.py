import numpy as np

from benchmarks.layers._shared import make_module


def make_weights(case, inputs, rand):
    p = case["params"]
    return {"weight": rand((p["vocab_size"], p["embedding_dim"]))}


def reference(case, inputs, weights):
    return weights["weight"][np.asarray(inputs[0], dtype=np.int64)]


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.embedding(x, layer.weight)

    return make_module(weights, forward)
