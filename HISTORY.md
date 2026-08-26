# HISTORY.md

This is the narrative companion to [CLAUDE.md](CLAUDE.md): bug postmortems, dated incidents, rejected alternatives, and the reasoning behind decisions that CLAUDE.md only states as a rule today. Each entry names the CLAUDE.md section it relates to, so you can jump between the two.

Read this when you want to know *why* something is the way it is, or when you're about to reintroduce something that was already tried and reverted.

Dated entries below are in chronological order, oldest first — append new entries at the end. Entries whose original date was never recorded are kept together in **Undated**, right below, rather than guessed into the timeline.

## Undated

**No SCHEDULER plugin imports `main.py`** (relates to: "State machine" in CLAUDE.md) — earlier, the default scheduler used to poll `state.is_running` in its own internal wait loop, which required a late/deferred `from .. import main` (a top-level `from ..main import state` fails at import time with a circular-import error, since `main.py` imports the plugin registry before defining `state`). Removing the internal wait (SCHEDULER now returns immediately with `target=None` instead of sleeping) removed the need for that coupling entirely.

**Why `internal`/`indi` aren't nested subdirectories** (relates to: "Plugin system" in CLAUDE.md) — explicitly rejected: nesting `internal`/`indi` as a subdirectory inside each sub-plugin (`slew/internal/slew.py`) — the user's call: it forces a full duplicate implementation for zero benefit, since `internal` doesn't need first-class-citizen status. A future `ascom` backend, if it ever needs its own duplicate `<name>_ascom.py` per sub-plugin, is the concrete trigger that would justify resolving the backend axis at the device layer instead — not decided yet, deliberately deferred.

**INDI hardware drivers' POCS heritage** (relates to: "INDI hardware drivers" in CLAUDE.md) — `hardware_indi/` is adapted from the PANOPTES/POCS observatory control software: the POCS-specific config loader and MongoDB-backed database layer that the original `Base` class used to pull in on every instantiation have been stripped out (`wrapper/base.py` is now just a logger), since Rosace has its own config/databases that have nothing to do with POCS's.

**FITS header comments — the KStars `/` truncation quirk** (relates to: "Coordinate frame convention" in CLAUDE.md) — comments stamped by `capture_guide_image()` deliberately never contain a literal `/` character. Found empirically that KStars' FITS header viewer truncates a comment at the first `/`, even one that's just part of the comment text and not a real FITS delimiter. The FITS standard itself only treats the *first* `/` after the value as the comment separator, but KStars doesn't respect that distinction.

**GUI — x4js quirks found while building the login form** (relates to: "GUI" in CLAUDE.md) — two non-obvious x4js behaviors were discovered the hard way: `Button._on_click()` always calling `preventDefault()`/`stopPropagation()` regardless of whether a `click` prop was given (breaking native submit-on-click inside a `Form`), and `Form` extending `Box` rather than `VBox` (so `display:flex` isn't inherited and must be declared explicitly in that component's own stylesheet). Also from this era: `setMainView()` cannot be called a second time to swap the root view — its `mounted` guard is declared in `core_application.ts` but never actually set to `true` anywhere, confirmed by reading the source rather than assumed — which is why `AppShell` swaps content via `setContent()` instead.

## 2026-08-05 — The `Observation.hardware` deep-copy crash

*Relates to: "Wired into SEQUENCER" (hardware handling) in CLAUDE.md.*

A whole session silently stopped processing after its first observation, `state.is_running` stuck `True` forever, GUI showing Start and Stop both enabled. Root cause: `obs.model_copy(deep=True, update={"hardware": {}})` does **not** skip deep-copying fields named in `update` — it deep-copies everything first and only overwrites afterward, so this line still ran `copy.deepcopy()` on the live INDI adapters, which raised (confirmed empirically: `TypeError: cannot pickle '_thread.lock' object`), uncaught, silently killing the `session_runner()` background task (that line sits in the `for...else` right after the per-stage `try/except`, not inside it). Fixed by clearing `obs.hardware` to `{}` *before* calling `model_copy(deep=True)` (no `update` needed) and restoring it after, wrapped in its own `try/except` so a future failure here logs and moves on to the next observation instead of taking the whole session down silently.

## 2026-08-09 — Coordinate frame convention made explicit, JNow leftover removed

*Relates to: "Coordinate frame convention: J2000/ICRS everywhere" in CLAUDE.md.*

The J2000/ICRS-everywhere convention was made a deliberate, explicit, app-wide decision on this date. As part of the same cleanup, a leftover `equatorial_eod: J2000 # JNOW` key was removed from both hardware YAMLs (`conf/hardware_indi_72ED.yaml`, `conf/hardware_indi_simu.yaml`) under `mount:` — it was never read by any code (confirmed via `grep`) and only invited confusion about where the J2000/JNow boundary actually is (it's a hardcoded fact of `IndiMount`, not a per-installation setting).

## 2026-08-13 — Casiers are not a flow-tracking mechanism

*Relates to: "Plugin system" (casiers) in CLAUDE.md.*

Added after real GUI logs showed sub-plugins re-checking a previous step's casier just to confirm "did it succeed" — redundant, since the orchestrator's own `try/except` around every `execute()` call already guarantees a failed step stops the whole stage before the next sub-plugin is ever reached. Applied across 7 sub-plugins plus the `blank_*` templates and CLAUDE.md itself.

## 2026-08-13 — Sub-plugin logging convention (log once, after success)

*Relates to: "Plugin system" (logging convention) in CLAUDE.md.*

From the same GUI-log review as the casier principle above: "Stopping PHD2 guiding" / "PHD2 guiding stopped" was two lines saying almost the same thing around one call — trimmed to just the second, which is what established the "log once, after success, not before+after" rule now in CLAUDE.md.

## 2026-08-13 — Session directory astronomical-day drift bug

*Relates to: "Two SQLite databases" (`time_helpers.py`) in CLAUDE.md.*

Until this date, `InitSessionPlugin` used a plain UTC calendar date to name the session directory instead of the site's local astronomical day — this silently drifted from `db_get_recent_observations()`'s own local-noon-to-noon boundary by several hours (the gap between UTC midnight and local noon) ever since the astronomical-day concept was introduced elsewhere in the app. `time_helpers.py` was created specifically to give both call sites (`InitSessionPlugin`'s directory naming and `database.py`'s DB queries) a single shared definition of "local noon to noon," so the two could never disagree again.

## 2026-08-14 — 72ED Alpy600 light-source states confirmed on real hardware

*Relates to: "Generic device interfaces and INDI adapters" in CLAUDE.md.*

`hardware_indi/devices/spectro_Alpy600.py::IndiSpectroAlpy600` drives the Alpy600's light source through the Stellarmate SM Pro powerbox's switch vector `POWER_CHANNELS` (channels 3/4) rather than any dedicated spectroscope protocol (none exists for this instrument). All four light-source states (SKY/DARK/CALIB/FLAT) were confirmed against the real instrument on this date.

## 2026-08-15 — `CCD_COOLER` vs `CCD_TEMPERATURE` are separate properties

*Relates to: "Science camera temperature and the dark-frame library" in CLAUDE.md.*

The cooler on/off switch (INDI's `CCD_COOLER`) is a separate property from the temperature setpoint (`CCD_TEMPERATURE`) on many cameras — the setpoint alone has no physical effect until the cooler is actually switched on. Found the hard way testing against the INDI CCD Simulator: the setpoint accepted and echoed back regardless of cooler state there, masking the gap until real-hardware testing would have hit it.

## 2026-08-16 — AstroPy/IERS dropped for the scheduler's altitude check

*Relates to: "The default scheduler: scheduler_rosace_2026" in CLAUDE.md.*

`scheduler_rosace_2026`'s altitude check originally relied on AstroPy's `AltAz` transform. That path depends on IERS Earth-orientation tables: with `auto_download` disabled (the observing site has no guaranteed internet access), AstroPy's default `IERS_Auto` table (IERS-A, predicted values) goes stale after `conf.auto_max_age` (30 days) and raises — found in production on this date, `INTERPOLATE_ERROR`/`ValueError` on every single call, so the SCHEDULER could never select a target at all. Forcing `IERS_B` (measured-only) avoided the crash but left two permanent `AstropyWarning`s, since IERS-B is by design always weeks behind "now" — no snapshot freshness could ever close that gap. Neither variant fit an unattended site with unreliable connectivity, so the whole IERS dependency was dropped for this one calculation: the new `astro_math.py` module (Meeus' low-precision GMST formula + the standard altitude formula, plain `math`/`datetime`) is accurate to a few arcminutes (cross-checked against AstroPy's own `AltAz` during development, worst case ~0.25° off) — orders of magnitude better than `MIN_TARGET_ALTITUDE_DEG` needs, with zero external data dependency and zero maintenance burden. 594/594 tests passing at the time.

## 2026-08-25 — Camera cooldown target-temperature safety margin

*Relates to: "Science camera temperature and the dark-frame library" in CLAUDE.md.*

Found the hard way on a first real-sky test: a weak enough `cooling_capability` let the naive rounded-up target temperature land at or above ambient itself, which a cooling-only camera can never reach — this timed out the cooldown wait and failed `INIT_SESSION`. Fixed by clamping the computed target to never come within `MIN_COOLING_MARGIN_DEG_C` (5°C) of ambient; when the naive target doesn't leave that margin, the next colder multiple of 5 is used instead. Landed alongside new DARK/BIAS reuse tolerances (2°C / 1s), 619/619 tests passing.

## 2026-08-26 — `home_sync_am3`'s SYNC call was silently corrupting every subsequent GOTO

*Relates to: "Wired into SEQUENCER" (`home_sync_am3.py`) in CLAUDE.md.*

Real-hardware prep surfaced a bug: with the mount in "unparked" state (confirmed via `is_parked` reading correctly `False`), the telescope nonetheless stayed near its parking position through an entire simulated session instead of slewing to real targets.

Root cause, found by driving the live INDI Telescope Simulator directly (not just reading code): `home_sync_am3`'s original implementation called `mount.sync_to_coordinates(ra, dec)` after park()+unpark(), to tell the driver "you are now at Home's known sky coordinate." On this simulator (and potentially the real AM3 driver, if it also implements the shared INDI Alignment Subsystem library), a SYNC command doesn't just recalibrate the driver's position reference — it silently records a persistent alignment point and activates the driver's own pointing-correction model (`ALIGNMENT_SUBSYSTEM_ACTIVE`, "Nearest Math Plugin"). Every subsequent GOTO then gets "corrected" against that single point, dragging the mount back toward the sync/home region instead of the real target.

Empirical proof (via a script driving `IndiMount` directly against the running simulator):
- With alignment active (after a SYNC): a slew to RA 100°/DEC 20° landed near RA 113°/DEC 32° — pulled toward the sync position, tens of degrees off target.
- With alignment disabled, or with no SYNC ever issued: the same slew landed within ~3 arcseconds of the requested target.
- A plain park()+unpark(), with **no** SYNC at all, already gives the driver a position matching Rosace's own `astro_math`-computed Home coordinate to within 0.0000001° — the driver derives Home's sky position itself from time + configured site, exactly like Rosace's own code does. So the SYNC call was not just risky but strictly unnecessary.

The fix: `home_sync_am3` now only calls `park()`+`unpark()`, never SYNC. The user's framing of the fix, worth keeping in mind for any future mount-recovery work: building a pointing-correction model on purpose is one thing (a deliberate, multi-point calibration procedure); feeding that model from routine re-homing is another, and a recovery step like this must never do the latter.

As a consequence, `sync_to_coordinates()`/`get_home_position()` were removed as dead code from the `Mount` Protocol (`hardware/mount.py`), `MountAdapter` (`hardware_indi/adapters/mount.py`), and `IndiMount` (`sync_to_coord()`/`get_home_position_icrs()`, `hardware_indi/devices/mount.py`) — no other caller existed. If a future need for an explicit manual sync resurfaces (e.g. a GUI-triggered recalibration), re-add it deliberately then, with the alignment-subsystem risk above in mind rather than assuming SYNC is a harmless position marker.
