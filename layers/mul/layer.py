from layers._shared import make_module


def reference(case, inputs, weights):
    return inputs[0] * inputs[1]


def module(case, weights):
    def forward(layer, x, *rest):
        return x * rest[0]

    return make_module(weights, forward)
