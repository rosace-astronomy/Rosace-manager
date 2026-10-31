# Changelog

All notable changes to Rosace, for someone using it. Versions are numbered
X.Y.Z.n (see CLAUDE.md, "Versioning"); only published versions (X.Y.Z.0)
are listed, newest first. The reasons behind the changes are in
[HISTORY.md](HISTORY.md).

## [Unreleased]

### Added

- The Simbad servers used to import stars are set in the configuration,
  tried in order: when the first does not answer, the next one is used, and
  the rest of an import list no longer waits for the dead server star after
  star. **The key `simbad_tap_urls` is required**: add it to your
  `config.yaml` (copy it from the packaged `conf/config.yaml`: the CDS in
  Strasbourg, then its mirror at Harvard), or Rosace does not start.
- The demo's Docker image is published on Codeberg's container registry:
  `docker run ... codeberg.org/francoiscochard/rosace-demo`, nothing to
  build (see `docker/README.md`).
- The `rosace` command works (it failed at once before): `rosace` starts
  the server, with the same options as `python -m rosace.main`.
- `--port` and `--host` choose where the server listens, e.g.
  `rosace --demo-mode --port 8001` beside a real server.
- `--version` prints the version; the server's first log line gives the
  version and the configuration in use.
- `rosace --init-config` creates `~/.config/rosace/config.yaml`, a copy of
  the simulators configuration to adapt to the installation.
- `rosace --purge-failed <date>` removes what a night's failed acquisitions
  left (database rows, guide images, observation files), keeping every
  other observation complete; without `--apply`, only lists what would go.
- Tab completion of the `rosace` options (one line to add to `~/.bashrc`,
  see README.md).

### Changed

- An installation's own configuration now lives in `~/.config/rosace/`
  (`config.yaml`, and its hardware profiles next to it), outside the code:
  a bare `rosace` starts it, or the INDI simulators when there is none.
  `conf/base_data_dir.yaml` and the `~/.rosace/conf/config.yaml` lookup are
  gone. `rosace --check-config` also names the hardware profile found.
- A mistake on the command line (unknown option, typo, bad port, options
  that do not go together) is reported clearly, with exit code 2, instead of
  a Python traceback or being silently ignored; `--option=value` is accepted.
- `POST /programs` refuses a name already taken, whatever its case (HTTP
  409 « Programme déjà existant »), instead of creating a second program of
  the same name. Looking a program or a star up by name no longer treats
  `_` or `%` as wildcards (`A_B` could find `AxB`, even for a `DELETE`).
- The API page `/docs` (and `/redoc`) asks to log in to the interface first,
  with a link, instead of showing operations that then all fail with 401.
- The terminal where Rosace runs no longer shows a line for every web
  request (`GET /status ...`), nor Rosace's DEBUG lines: only what the
  interface's Journal shows. `rosace.log` still holds the DEBUG lines.

## [0.1.0.0] — 2026-10-25

First numbered version. Rosace at this point:

- Runs a whole night on its own, from target selection to archiving, and
  stops cleanly when asked; start and stop can be scheduled at a time of day.
- Web interface: session control, observations list with live step
  progress, log, guide image, 2D and 1D spectrum viewers; login accounts.
- Target catalogue and scientific programs, with Simbad import.
- Multi-program selector: one planner per program (Be stars, random pick,
  hand-written observation list), time shares, local horizon mask,
  meridian filter.
- 72ED setup (AM3 mount, Alpy 600, INDI, PHD2): measured homing, pointing
  corrected by plate solving, slit position measured on each observation,
  PHD2 guiding onto the slit, guiding-camera focus measurement, recovery
  from a dead guiding camera.
- Science camera cooled to a per-night temperature; dark, bias, lamp and
  flat series reused when possible, dark library by temperature.
- Native reduction of Be star spectra down to a BeSS-compliant FITS:
  masters, preprocessing with hot-pixel replacement, tilt and smile,
  extraction, stacking, dispersion law, wavelength calibration, response,
  scaling, crop; resolving power, signal-to-noise ratio and guiding
  statistics in the header; plots and a summary PDF per observation.
- PHD2 guide log rebuilt for each observation; FRIPON all-sky image and
  wide-field image of the target's field.
- BeSS validation page (`/be/`): each Be spectrum checked by the observer,
  compared with the star's earlier Rosace and BeSS spectra.
- Export to Shelyak's Demetra software.
- Demo on replayed hardware (`--demo-mode`), and its Docker image.
