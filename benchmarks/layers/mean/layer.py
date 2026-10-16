from benchmarks.layers._shared import make_module


def reference(case, inputs, weights):
    return inputs[0].mean(axis=-1, keepdims=True)


def module(case, weights):
    def forward(layer, x, *rest):
        return x.mean(-1, keepdim=True)

    return make_module(weights, forward)
