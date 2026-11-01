"""105 batches of examples/libprep_capture.py (96 samples each: 10,080 samples), for the
generated line workcell examples/libprep_10k.yaml."""
from libprep_capture import libprep_capture


def batch():
    return [libprep_capture(batch=b) for b in range(1, 106)]
