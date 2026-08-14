# Rosace — demo container.
#
# This image is for evaluation/demo purposes (SEQUENCER_BACKEND=internal,
# no real hardware) — see docker/config.demo.yaml. It is not how a real
# observatory deployment is meant to run.

# ── Stage 1: build the GUI (x4js, esbuild via x4build) ─────────────────
FROM node:20-slim AS gui-builder

WORKDIR /gui

# package-lock.json is .gitignore'd in this repo (see gui/.gitignore), so it
# usually won't exist in a fresh checkout — `npm install` (not `npm ci`)
# handles both cases.
COPY rosace/rosace/gui/package.json rosace/rosace/gui/package-lock.jso[n] ./
RUN npm install

COPY rosace/rosace/gui/src ./src
RUN npm run build

# ── Stage 2: the Python application ─────────────────────────────────────
FROM python:3.12-slim AS app

WORKDIR /app

# pyindi-client (a mandatory rosace dependency, even though this demo's
# SEQUENCER_BACKEND=internal never touches it at runtime — see
# hardware_indi/) itself pulls in dbus-python, which has no prebuilt wheel
# and needs a C compiler + libdbus/glib headers to build from source. Without
# these, pip's build step fails outright (meson can't find a compiler).
# Kept simple deliberately: not removed afterward, so the final image carries
# the compiler toolchain it no longer needs (~150-250MB) — fine for a demo
# image, worth trimming with a proper build stage if that ever matters.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libdbus-1-dev libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

COPY rosace/pyproject.toml /app/rosace/pyproject.toml
COPY rosace/rosace /app/rosace/rosace

# Editable install, deliberately: pyproject.toml has no package_data/
# MANIFEST.in for the non-Python files (conf/*.yaml, programmes/*.yaml,
# gui/bin/*) — a regular `pip install` would silently drop them from
# site-packages. Editable install keeps the source tree itself as what's
# imported, sidestepping that gap entirely (same as the documented dev
# workflow: `pip install -e "rosace/[dev]"`).
RUN pip install --no-cache-dir -e /app/rosace

COPY --from=gui-builder /gui/bin /app/rosace/rosace/gui/bin

# Demo-only overrides — kept in their own directory, outside rosace/, so
# they're obviously not part of the real application.
COPY docker/config.demo.yaml docker/seed_demo_data.py docker/entrypoint.sh /app/docker/
RUN chmod +x /app/docker/entrypoint.sh

# seed_demo_data.py seeds targets.db programs named "Be_stars"/"PN" (a
# realistic catalogue) — pipeline_loader.py resolves a program name directly
# to a programs/<name>.yaml file, which must be backend-agnostic since this
# image runs sequencer_backend=internal (no real hardware). The real
# programs/Be_stars.yaml drives the actual 72ED pipeline (pointing_72ed) and
# is unusable here; programs/PN.yaml doesn't exist at all in the real app.
# These demo-only overrides (same steps as demo.yaml) replace/add them in the
# image only — the real files on disk are untouched.
COPY docker/demo_programs/Be_stars.yaml docker/demo_programs/PN.yaml /app/rosace/rosace/programs/

ENV ROSACE_CONFIG=/app/docker/config.demo.yaml
ENV PYTHONUNBUFFERED=1

# Databases, log, and observation files — see config.demo.yaml's
# base_data_dir. Mount a named volume here to keep the demo catalogue and
# observation history across restarts.
VOLUME /data

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
