"""
SEQUENCER sub-plugin package.

One directory per sub-plugin (slew/, focus/, star_spectra_acquisition/),
each with its own class and tests. The bare name (<name>/<name>.py) is the
canonical version, used when SEQUENCER_BACKEND="indi"; "internal" is a demo
exception (<name>/<name>_internal.py, only where it's actually useful — not
a full-fledged backend). pipeline_loader.py resolves dynamically, one
import per step declared in the active program YAML — this module
therefore doesn't import anything itself.
"""
