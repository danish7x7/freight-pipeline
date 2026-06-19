"""The showcase demo: seed a sample order and run the REAL pipeline on it.

The console's "load sample order" feature flows a fixed corpus sample through the same
server-side write path, the same deterministic validation gate, the same rate engine,
and the same finalize as a real inbound email — so a visitor can watch the injection
defense work without a real inbox. Only the model's extraction OUTPUT is recorded (a
fixed corpus value); the gate, pricing, and persistence run live. See ``service.py``.
"""

from freight.demo.service import (
    DEMO_SAMPLES,
    DemoResult,
    SampleName,
    run_demo_sample,
)

__all__ = ["DEMO_SAMPLES", "DemoResult", "SampleName", "run_demo_sample"]
