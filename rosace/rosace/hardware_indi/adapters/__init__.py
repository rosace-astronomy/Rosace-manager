"""
Async adapters bridging hardware_indi/'s blocking device classes to the
generic hardware/ interfaces (Mount, Camera, Focuser, FilterWheel).

Each adapter wraps an already-connected device instance and runs its
blocking calls via asyncio.to_thread — the same bridging technique already
used by plugins/hardware_connectivity.py, just moved inside the adapter
instead of at the call site, so the generic interfaces stay uniformly
async regardless of backend (see hardware_ascom/, not yet implemented,
which will use native async HTTP instead).
"""
