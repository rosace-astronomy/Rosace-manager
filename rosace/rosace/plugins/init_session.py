import asyncio
import logging

from ..config import OBSERVATIONS_DIR, SEQUENCER_BACKEND, SITE_NAME
from ..models import SessionContext
from ..time_helpers import astronomical_day_local_date
from .camera_cooldown import activate_cooling_for_every_camera, cool_science_camera_to_target
from .hardware_connectivity import connect_session_hardware

log = logging.getLogger("spectro.plugin.init_session")


class InitSessionPlugin:
    """Initializes the session: session directory, instrument connections, pre-night checks."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        log.info(f"[INIT_SESSION] Starting session {ctx.session_id}")

        date_str = astronomical_day_local_date()
        safe_site = SITE_NAME.replace(" ", "_").replace("/", "-")
        session_dir = OBSERVATIONS_DIR / f"{date_str}_{safe_site}"
        session_dir.mkdir(parents=True, exist_ok=True)
        ctx.session_dir = str(session_dir)
        log.info(f"[INIT_SESSION] Session directory: {session_dir}")

        if SEQUENCER_BACKEND == "indi":
            log.info("[INIT_SESSION] Connecting hardware (INDI devices + PHD2)…")
            connected = await connect_session_hardware()
            ctx.hardware = connected.adapters
            ctx.hardware_config = connected.hardware_config
            log.info(f"[INIT_SESSION] Hardware connected: {list(ctx.hardware)}")

            await activate_cooling_for_every_camera(ctx.hardware, ctx.hardware_config)

            science_camera_config = ctx.hardware_config.get("science_camera", {})
            if science_camera_config.get("cooling"):
                log.info("[INIT_SESSION] Cooling science camera to session target temperature…")
                ambient, target = await cool_science_camera_to_target(
                    camera=ctx.hardware["science_camera"],
                    focuser=ctx.hardware["focuser"],
                    cooling_capability_deg_c=science_camera_config["cooling_capability"],
                    tolerance_deg_c=science_camera_config["temperature_tolerance_deg_c"],
                    stop_check=ctx.stop_check,
                )
                ctx.ambient_temperature_deg_c = ambient
                ctx.camera_target_temperature_deg_c = target
        else:
            log.info("[INIT_SESSION] Running in DEMO mode (sequencer_backend=internal) — no hardware involved")

        await asyncio.sleep(1.0)
        log.info("[INIT_SESSION] Session initialised")
        return ctx
