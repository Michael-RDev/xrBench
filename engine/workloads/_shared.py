def make_module(weights, forward):
    import torch

    class Layer(torch.nn.Module):
        def __init__(self):
            super().__init__()
            for name, value in weights.items():
                self.register_buffer(name, torch.from_numpy(value.copy()))

        def forward(self, x, *rest):
            return forward(self, x, *rest)

    return Layer().eval()
