"""
REDUCTION sub-plugin package.

One directory per sub-plugin (bias/, dark/, flat/, extract/), each with its
own class and tests. The pipeline is loaded dynamically by
pipeline_loader.py, one import per step declared in the active program
YAML — this module therefore doesn't import anything itself.
"""
