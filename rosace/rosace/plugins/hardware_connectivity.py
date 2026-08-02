"""
Hardware connectivity check for INIT_SESSION (SEQUENCER_BACKEND=indi only).

Reads the active hardware profile (conf/hardware_<variant>.yaml, resolved as
HARDWARE_CONFIG_PATH) into a plain dict and, for every declared INDI device
plus PHD2, attempts a single connection bounded by
HARDWARE_CONNECT_TIMEOUT_SECONDS. Each device's driver class is loaded
dynamically from its own module/module_dir/class_name fields, then
instantiated and connected using nothing but its own config sub-dict.

Devices are connected via blocking (non-asyncio) code inherited from
hardware_indi/, so each device check runs in a worker thread
(asyncio.to_thread) to avoid blocking the event loop. Devices sharing the
same INDI server (indi_host/indi_port) also share a single IndiClient
instance under the hood (see hardware_indi/wrapper/indi_client.py's
SingletonIndiClientHolder) whose setup is not thread-safe, so device checks
run one at a time rather than concurrently. PHD2 uses a plain asyncio TCP
connection and runs concurrently with the device checks.

Devices are disconnected again right after a successful check: this
function only verifies reachability, it doesn't keep connections open for
the rest of the session (SEQUENCER's own connection lifecycle is a separate,
not yet implemented, concern - see sequencer_plugins/indi/, still
placeholders).
"""

from __future__ import annotations

import asyncio
import importlib
import logging

import yaml

from ..config import HARDWARE_CONFIG_PATH, HARDWARE_CONNECT_TIMEOUT_SECONDS

log = logging.getLogger("spectro.plugin.hardware_connectivity")


def _load_hardware_config() -> dict:
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_device_class(module_dir: str, module: str, class_name: str):
    mod = importlib.import_module(f"{module_dir}.{module}")
    return getattr(mod, class_name)


def _connect_device_sync(device_cfg: dict, timeout: float) -> None:
    """Blocking: instantiate the device's driver class and connect it. Runs in a worker thread."""
    cls = _load_device_class(device_cfg["module_dir"], device_cfg["module"], device_cfg["class_name"])
    device = cls(config=device_cfg, connect_on_create=False)
    device.timeout = timeout
    device.connect()
    device.disconnect()


async def _check_devices(devices: dict[str, dict], timeout: float) -> list[str]:
    """Connects every device one at a time (they may share a single, non-thread-safe
    IndiClient behind the scenes) and returns the list of failure messages."""
    failures = []
    for device_name, device_cfg in devices.items():
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_connect_device_sync, device_cfg, timeout),
                timeout=timeout,
            )
            log.info(f"[INIT_SESSION] Device '{device_name}' reachable")
        except Exception as e:
            failures.append(f"device '{device_name}': {e}")
    return failures


async def _check_phd2(phd2_cfg: dict, timeout: float) -> str | None:
    host, port = phd2_cfg["host"], phd2_cfg["port"]
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        log.info(f"[INIT_SESSION] PHD2 reachable at {host}:{port}")
        return None
    except Exception as e:
        return f"PHD2 ({host}:{port}): {e}"


async def check_hardware_connectivity() -> None:
    """Verifies every declared INDI device and PHD2 answer within HARDWARE_CONNECT_TIMEOUT_SECONDS.
    Raises RuntimeError listing every failure if at least one check fails."""
    hw_config = _load_hardware_config()
    timeout = HARDWARE_CONNECT_TIMEOUT_SECONDS

    devices_task = asyncio.create_task(_check_devices(hw_config.get("INDI_drivers", {}), timeout))
    phd2_task = (
        asyncio.create_task(_check_phd2(hw_config["PHD2_config"], timeout))
        if "PHD2_config" in hw_config
        else None
    )

    device_failures = await devices_task
    phd2_failure = await phd2_task if phd2_task else None

    failures = device_failures + ([phd2_failure] if phd2_failure else [])
    if failures:
        raise RuntimeError(f"Hardware connectivity check failed: {'; '.join(failures)}")
