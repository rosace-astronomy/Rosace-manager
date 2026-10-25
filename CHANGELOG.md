# Changelog

All notable changes to Rosace, for someone using it. Versions are numbered
X.Y.Z.n (see CLAUDE.md, "Versioning"); only published versions (X.Y.Z.0)
are listed, newest first. The reasons behind the changes are in
[HISTORY.md](HISTORY.md).

## [Unreleased]

### Added

- The demo's Docker image is published on Codeberg's container registry:
  `docker run ... codeberg.org/francoiscochard/rosace-demo`, nothing to
  build (see `docker/README.md`).

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
