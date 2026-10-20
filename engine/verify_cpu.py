"""Execute exported CPU fixtures once on the host; this is not XR qualification."""
import json
from pathlib import Path

from engine.paths import BUILD


def verify(bundle=BUILD/'bundle'):
    import numpy as np
    import torch
    from executorch.runtime import Runtime
    torch.set_num_threads(1)
    bundle=Path(bundle)
    suite=json.loads((bundle/'suite.json').read_text())
    runtime=Runtime.get()
    checked=[]
    for case in suite['cases']:
        export=case['backends']['executorch_cpu']
        if export['export_status']!='ready':raise ValueError('Missing CPU export: '+case['id'])
        inputs=[torch.from_numpy(np.fromfile(bundle/i['file'],dtype='<i8' if i['dtype']=='int64' else '<f4').reshape(i['shape'])) for i in case['inputs']]
        program=runtime.load_program(bundle/export['program'])
        result=program.load_method('forward').execute(inputs)[0].numpy()
        expected=np.fromfile(bundle/case['expected'],dtype='<f4').reshape(case['output_shape'])
        if result.shape!=expected.shape or not np.allclose(result,expected,atol=suite['atol'],rtol=suite['rtol'],equal_nan=False):
            raise AssertionError('CPU runtime correctness failed: '+case['id'])
        checked.append(case['id'])
    print(json.dumps(dict(checked=len(checked),scope='Host CPU correctness only; no XR hardware qualification',cases=checked)))
    return checked


if __name__=='__main__':verify()
