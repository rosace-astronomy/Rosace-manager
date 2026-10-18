# Rosace — demo container.
#
# Runs Rosace's demo mode (python -m rosace.main --demo-mode): the real Be
# stars process -- pointing, plate solving, guiding, acquisition, the whole
# native reduction -- on replayed hardware, which hands back the frames of a
# real night instead of driving a telescope (see conf/config_demo.yaml,
# conf/hardware_replay.yaml and CLAUDE.md, "Trying Rosace without
# hardware"). Not how a real observatory installation is meant to run.
#
# The replay dataset (docker/replay_data/, git-ignored) must be built first
# with scripts/build_demo_dataset.py; it is anonymized (approximate site, no
# names), and so is everything else copied here (see .dockerignore).

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

# - astrometry.net: solve-field, the plate solver of the pointing steps (its
#   index tiles come with the replay dataset, see /etc/astrometry.cfg below);
# - pyindi-client (a mandatory rosace dependency, never used by the replay
#   devices) pulls in dbus-python, which has no prebuilt wheel and needs a C
#   compiler + libdbus/glib headers to build. Kept in the final image (a few
#   hundred MB) for simplicity.
RUN apt-get update && apt-get install -y --no-install-recommends \
        astrometry.net build-essential pkg-config libdbus-1-dev libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, from pyproject.toml alone: this layer is rebuilt only
# when the dependencies change, not at every code change.
COPY rosace/pyproject.toml /app/rosace/pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/app/rosace/pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# The replay dataset (~490 MB): its own layer, before the code, so a code
# change does not copy it again. solve-field reads its index tiles there.
COPY docker/replay_data /opt/rosace-demo/replay
RUN printf 'inparallel\nadd_path /opt/rosace-demo/replay/astrometry\nautoindex\n' > /etc/astrometry.cfg

# The application. Editable install, deliberately: pyproject.toml has no
# package_data for the non-Python files (conf/*.yaml, programs/*.yaml,
# data/*.csv, gui/bin/*) — a regular install would drop them.
COPY rosace/rosace /app/rosace/rosace
RUN pip install --no-cache-dir --no-deps -e /app/rosace

COPY --from=gui-builder /gui/bin /app/rosace/rosace/gui/bin

COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

ENV PYTHONUNBUFFERED=1

# The demo's data (databases, log, observations): config_demo.yaml's
# base_data_dir. A named volume here keeps them across restarts.
VOLUME /root/.rosace-demo

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
