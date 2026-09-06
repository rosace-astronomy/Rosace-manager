"""
Hardware connectivity for INIT_SESSION (SEQUENCER_BACKEND=indi only).

connect_session_hardware() reads the active hardware profile
(conf/hardware_<variant>.yaml, resolved as HARDWARE_CONFIG_PATH) into a
plain dict and, for every declared INDI device, connects it for real and
keeps the connection open for the whole session — the caller (INIT_SESSION)
is responsible for handing the result to CLOSE_SESSION so each adapter gets
disconnected there. Each device's driver class is loaded dynamically from
its own module/module_dir/class_name fields, then instantiated and
connected using nothing but its own config sub-dict, bounded by
HARDWARE_CONNECT_TIMEOUT_SECONDS. The connected device is wrapped in the
adapter matching its declared `role` field (see hardware/ for the generic
interfaces those adapters satisfy) — a device whose role has no registered
adapter yet is connected and logged, not fatal, but isn't included in the
returned mapping since nothing could call it through hardware/ yet.

Devices are connected via blocking (non-asyncio) code inherited from
hardware_indi/, so each device connect runs in a worker thread
(asyncio.to_thread) to avoid blocking the event loop. Devices sharing the
same INDI server (indi_host/indi_port) also share a single IndiClient
instance under the hood (see hardware_indi/wrapper/indi_client.py's
SingletonIndiClientHolder) whose setup is not thread-safe, so devices are
connected one at a time rather than concurrently. PHD2 is connected via a
persistent phd2.Phd2Client (its own JSON-RPC protocol, genuinely async —
no worker thread needed) and runs concurrently with the device connects;
the connected client is returned under the "phd2" key alongside the INDI
device adapters (no hardware/ Protocol/adapter split for it — see
phd2/__init__.py for why).

Known simplification: if one device fails partway through, devices already
connected before it are left connected (not explicitly disconnected here)
since INIT_SESSION failure already stops the session as a critical error —
acceptable for now, revisit if leaked connections become a real problem.

Each device's YAML entry is also split, implicitly, into two halves: a
closed set of "plumbing" fields this module and every device class need to
connect at all (_PLUMBING_KEYS -- role/module/module_dir/class_name/
indi_client/indi_name), and everything else, which is that device's own
params -- untouched, unfiltered, never named here (see
_extract_device_params()). Adding a new field to a device's YAML entry
(e.g. a camera's cooling_capability, a focuser's focus_range) makes it
reach SEQUENCER sub-plugins automatically, via Observation.hardware_config,
with zero code change in this module or anywhere else -- the same dynamic,
config-driven spirit as pipeline_loader.py's sub-plugin resolution.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..config import HARDWARE_CONFIG_PATH, HARDWARE_CONNECT_TIMEOUT_SECONDS
from ..hardware_indi.adapters.camera import CameraAdapter
from ..hardware_indi.adapters.filter_wheel import FilterWheelAdapter
from ..hardware_indi.adapters.focuser import FocuserAdapter
from ..hardware_indi.adapters.mount import MountAdapter
from ..hardware_indi.adapters.spectroscope import SpectroscopeAdapter
from ..phd2.phd2_client import Phd2Client

log = logging.getLogger("spectro.plugin.hardware_connectivity")

_ADAPTERS_BY_ROLE = {
    "mount": MountAdapter,
    "camera": CameraAdapter,
    "focuser": FocuserAdapter,
    "filter_wheel": FilterWheelAdapter,
    "spectroscope": SpectroscopeAdapter,
}

# Closed list of "plumbing" fields every device entry must declare, regardless
# of role -- needed either by this module itself (role/module/module_dir/
# class_name, for dynamic loading and adapter selection) or by every IndiDevice
# subclass's own __init__ (indi_client, indi_name -- the device's own INDI
# identification, required even by SpectroscopePlaceholder, which never uses
# it, deliberately, to keep this list free of per-role exceptions). Anything
# else a device declares is business config for SEQUENCER sub-plugins, not
# plumbing -- see _extract_business_config() below, which never names a
# single one of those fields, by design.
_PLUMBING_KEYS = {"role", "module", "module_dir", "class_name", "indi_client", "indi_name"}


@dataclass
class ConnectedHardware:
    """connect_session_hardware()'s return value: the connected adapters
    (SessionContext.hardware / Observation.hardware) alongside each device's
    own params (SessionContext.hardware_config / Observation.hardware_config)
    -- everything in its hardware YAML entry beyond the plumbing fields,
    verbatim, unfiltered."""
    adapters: dict[str, Any] = field(default_factory=dict)
    hardware_config: dict[str, dict[str, Any]] = field(default_factory=dict)


def _load_hardware_config() -> dict:
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_device_class(module_dir: str, module: str, class_name: str):
    mod = importlib.import_module(f"{module_dir}.{module}")
    return getattr(mod, class_name)


def _connect_device_sync(device_cfg: dict, timeout: float):
    """Blocking: instantiate the device's driver class and connect it (left open). Runs in a worker thread."""
    cls = _load_device_class(device_cfg["module_dir"], device_cfg["module"], device_cfg["class_name"])
    device = cls(config=device_cfg, connect_on_create=False)
    device.timeout = timeout
    device.connect()
    return device


def _extract_device_params(device_name: str, device_cfg: dict) -> dict:
    """Validates that device_cfg declares every plumbing field (_PLUMBING_KEYS),
    raising ValueError naming exactly what's missing if not, then returns
    everything else verbatim -- whatever a hardware YAML entry declares beyond
    the plumbing fields (cooling, cooling_capability, port, focus_range...)
    becomes that device's params, with no field named here. This is what lets
    a new hardware YAML field reach SEQUENCER sub-plugins without ever
    touching this module."""
    missing = _PLUMBING_KEYS - device_cfg.keys()
    if missing:
        raise ValueError(f"device '{device_name}': missing required field(s) {sorted(missing)}")
    return {k: v for k, v in device_cfg.items() if k not in _PLUMBING_KEYS}


async def _connect_devices(devices: dict[str, dict], timeout: float) -> tuple[dict[str, object], dict[str, dict], list[str]]:
    """Connects every device one at a time (they may share a single, non-thread-safe
    IndiClient behind the scenes), wraps each in its role's adapter, and extracts
    its params. Returns (adapters by device name, params by device name,
    failure messages) -- the two dicts share the same keys."""
    adapters: dict[str, object] = {}
    hardware_config: dict[str, dict] = {}
    failures = []
    for device_name, device_cfg in devices.items():
        try:
            device_params = _extract_device_params(device_name, device_cfg)
        except ValueError as e:
            failures.append(str(e))
            continue

        try:
            device = await asyncio.wait_for(
                asyncio.to_thread(_connect_device_sync, device_cfg, timeout),
                timeout=timeout,
            )
        except Exception as e:
            failures.append(f"device '{device_name}': {e}")
            continue

        role = device_cfg.get("role")
        adapter_cls = _ADAPTERS_BY_ROLE.get(role)
        if adapter_cls is None:
            log.warning(
                f"[INIT_SESSION] Device '{device_name}' (role={role!r}) connected, "
                f"no adapter registered for this role yet — not usable from hardware/"
            )
            continue
        adapters[device_name] = adapter_cls(device)
        hardware_config[device_name] = device_params
        log.info(f"[INIT_SESSION] Device '{device_name}' connected")

    return adapters, hardware_config, failures


async def _connect_phd2(phd2_cfg: dict, timeout: float) -> Phd2Client:
    """Connects to PHD2, loads its configured profile and engages its own
    equipment (Phd2Client.connect()) — left connected for the whole
    session, same as the INDI devices."""
    client = Phd2Client(
        phd2_cfg["host"], phd2_cfg["port"],
        settle=phd2_cfg["settle"], do_calibration=phd2_cfg.get("do_calibration", False),
    )
    await asyncio.wait_for(client.connect(profile_name=phd2_cfg.get("profile_name")), timeout=timeout)
    return client


async def connect_session_hardware() -> ConnectedHardware:
    """Connects every declared INDI device and PHD2 for the whole session
    (left open — the caller must disconnect each returned adapter/client at
    CLOSE_SESSION). Raises RuntimeError listing every failure if at least
    one device or PHD2 fails. Returns a ConnectedHardware: adapters keyed by
    device name (e.g. "science_camera", "mount") plus "phd2" — this is what
    becomes SessionContext.hardware / Observation.hardware — alongside each
    device's own params (SessionContext.hardware_config / Observation.
    hardware_config; PHD2 has no entry there, its own config shape doesn't
    match the per-INDI-device one)."""
    hw_config = _load_hardware_config()
    timeout = HARDWARE_CONNECT_TIMEOUT_SECONDS

    devices_task = asyncio.create_task(_connect_devices(hw_config.get("INDI_drivers", {}), timeout))
    phd2_task = (
        asyncio.create_task(_connect_phd2(hw_config["PHD2_config"], timeout))
        if "PHD2_config" in hw_config
        else None
    )

    adapters, hardware_config, failures = await devices_task

    if phd2_task is not None:
        try:
            adapters["phd2"] = await phd2_task
            log.info("[INIT_SESSION] PHD2 connected")
        except Exception as e:
            failures.append(f"PHD2: {e}")

    if failures:
        raise RuntimeError(f"Hardware connectivity check failed: {'; '.join(failures)}")

    return ConnectedHardware(adapters=adapters, hardware_config=hardware_config)
