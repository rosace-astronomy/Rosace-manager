"""
Replay devices: stand-ins for the real instrument, used by the demo mode.

A hardware profile (conf/hardware_replay.yaml) declares these classes in
place of the INDI ones, exactly like any other device class (module/
module_dir/class_name), and plugins/hardware_connectivity.py wraps them in
the usual hardware_indi/adapters/: they offer the same method names as the
INDI device classes. Every SEQUENCER sub-plugin then runs unchanged, but the
cameras hand back frames recorded on a real night instead of exposing.

- bench.py: the recorded dataset (built by scripts/build_demo_dataset.py)
  and the state the devices share (where the mount points, which light
  source the spectroscope selects) -- this is what picks each frame;
- devices.py: mount, cameras, focuser, spectroscope;
- fake_phd2.py: a PHD2 stand-in speaking PHD2's own JSON-RPC protocol over
  TCP, so the real phd2/phd2_client.py connects to it unchanged.
"""
