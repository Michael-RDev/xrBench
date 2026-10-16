from layers._shared import make_module


def reference(case, inputs, weights):
    return inputs[0].mean(axis=(-2, -1), keepdims=True)


def module(case, weights):
    import torch.nn.functional as F

    def forward(layer, x, *rest):
        return F.adaptive_avg_pool2d(x, (1, 1))

    return make_module(weights, forward)
