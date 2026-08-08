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
adapter yet (e.g. a spectroscope) is connected and logged, not fatal, but
isn't included in the returned mapping since nothing could call it through
hardware/ yet.

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
"""

from __future__ import annotations

import asyncio
import importlib
import logging

import yaml

from ..config import HARDWARE_CONFIG_PATH, HARDWARE_CONNECT_TIMEOUT_SECONDS
from ..hardware_indi.adapters.camera import CameraAdapter
from ..hardware_indi.adapters.filter_wheel import FilterWheelAdapter
from ..hardware_indi.adapters.focuser import FocuserAdapter
from ..hardware_indi.adapters.mount import MountAdapter
from ..phd2.phd2_client import Phd2Client

log = logging.getLogger("spectro.plugin.hardware_connectivity")

_ADAPTERS_BY_ROLE = {
    "mount": MountAdapter,
    "camera": CameraAdapter,
    "focuser": FocuserAdapter,
    "filter_wheel": FilterWheelAdapter,
}


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


async def _connect_devices(devices: dict[str, dict], timeout: float) -> tuple[dict[str, object], list[str]]:
    """Connects every device one at a time (they may share a single, non-thread-safe
    IndiClient behind the scenes) and wraps each in its role's adapter.
    Returns (adapters by device name, failure messages)."""
    adapters: dict[str, object] = {}
    failures = []
    for device_name, device_cfg in devices.items():
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
        log.info(f"[INIT_SESSION] Device '{device_name}' connected")

    return adapters, failures


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


async def connect_session_hardware() -> dict[str, object]:
    """Connects every declared INDI device and PHD2 for the whole session
    (left open — the caller must disconnect each returned adapter/client at
    CLOSE_SESSION). Raises RuntimeError listing every failure if at least
    one device or PHD2 fails. Returns the connected adapters, keyed by
    device name (e.g. "science_camera", "mount") plus "phd2" — this is what
    becomes SessionContext.hardware / Observation.hardware."""
    hw_config = _load_hardware_config()
    timeout = HARDWARE_CONNECT_TIMEOUT_SECONDS

    devices_task = asyncio.create_task(_connect_devices(hw_config.get("INDI_drivers", {}), timeout))
    phd2_task = (
        asyncio.create_task(_connect_phd2(hw_config["PHD2_config"], timeout))
        if "PHD2_config" in hw_config
        else None
    )

    adapters, failures = await devices_task

    if phd2_task is not None:
        try:
            adapters["phd2"] = await phd2_task
            log.info("[INIT_SESSION] PHD2 connected")
        except Exception as e:
            failures.append(f"PHD2: {e}")

    if failures:
        raise RuntimeError(f"Hardware connectivity check failed: {'; '.join(failures)}")

    return adapters
