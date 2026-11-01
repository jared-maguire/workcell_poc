"""105 batches of examples/libprep_capture.py (96 samples each: 10,080 samples), for the
generated line workcell examples/libprep_10k.yaml. Run them arriving through the day:

    .venv/bin/python -m workcell live examples/libprep_10k.yaml examples/libprep_10k.py \\
        --release-every 823
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libprep_capture import libprep_capture  # noqa: E402


def batch():
    return [libprep_capture(batch=b) for b in range(1, 106)]
