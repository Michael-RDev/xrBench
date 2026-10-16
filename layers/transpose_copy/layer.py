from layers._shared import make_module


def reference(case, inputs, weights):
    return inputs[0].swapaxes(-1, -2).copy()


def module(case, weights):
    def forward(layer, x, *rest):
        return x.transpose(-1, -2).contiguous()

    return make_module(weights, forward)
