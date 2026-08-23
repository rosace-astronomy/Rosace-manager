"""
SHARING sub-plugin package.

One directory per sub-plugin, like reduction_plugins/ — no backend axis (no
hardware dependency). Unlike SEQUENCER/REDUCTION, a program declares only
one (singular "sharing" key in the YAML, not a list): a program that needs
to share to several destinations handles that inside its own sub-plugin,
the mechanism itself isn't meant to support it. Resolved dynamically by
pipeline_loader.py::load_sharing_plugin() — this module doesn't import
anything itself.
"""
