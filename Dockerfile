# Rosace — demo container.
#
# Runs Rosace's demo mode (python -m rosace.main --demo-mode): the real Be
# stars process -- pointing, plate solving, guiding, acquisition, the whole
# native reduction -- on replayed hardware, which hands back the frames of a
# real night instead of driving a telescope (see conf/config_demo.yaml,
# conf/hardware_replay.yaml and CLAUDE.md, "Trying Rosace without
# hardware"). Not how a real observatory installation is meant to run.
#
# The replay dataset (docker/replay_data/, git-ignored, ~120 MB: 4
# observations, frames gzip-compressed) must be built first with
# scripts/build_demo_dataset.py; it is anonymized (approximate site, no
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

# ── Stage 2: the application, on Debian's own Python ────────────────────
# solve-field (astrometry.net, the plate solver of the pointing steps) runs
# Debian's Python (/usr/bin/python3 -m astrometry.util.image2pnm) on every
# image, so that Python and its numpy/astropy are here anyway: Rosace runs on
# it too, and the scientific libraries are installed once (Debian's numpy,
# scipy, astropy, matplotlib, dbus), not a second time by pip. photutils comes
# from pip: Rosace needs 3.0, Debian has 2.2. No compiler is needed:
# everything else comes as ready-made wheels.
FROM debian:trixie-slim AS app

WORKDIR /app

# Documentation, manual pages and translations left out (copyright files
# kept). Two packages fonttools (through matplotlib) depends on but Rosace
# never uses are removed after the install: sympy and the Unicode tables.
RUN printf 'path-exclude=/usr/share/doc/*\npath-include=/usr/share/doc/*/copyright\npath-exclude=/usr/share/man/*\npath-exclude=/usr/share/locale/*\npath-exclude=/usr/share/info/*\n' \
        > /etc/dpkg/dpkg.cfg.d/01-slim \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-numpy python3-scipy python3-astropy \
        python3-matplotlib python3-dbus astrometry.net ca-certificates \
    && rm -rf /var/lib/apt/lists/* /usr/lib/python3/dist-packages/sympy /usr/share/unicode

# A virtual environment that sees Debian's packages: pip only adds what they
# do not provide (FastAPI, uvicorn, pydantic, photutils, pyindi-client...), from
# pyproject.toml alone, so this layer is rebuilt only when the dependencies
# change. Wheels only (--only-binary): the build fails rather than compile.
# The libraries' own test folders are removed -- except astropy's (imported by
# astropy itself) and numpy's core one (imported by numpy.testing) --, and so
# are the compiled .pyc files (rebuilt when needed).
ENV PATH=/opt/venv/bin:$PATH
COPY rosace/pyproject.toml /app/rosace/pyproject.toml
RUN python3 -m venv --system-site-packages /opt/venv \
    && python3 -c "import tomllib; print('\n'.join(tomllib.load(open('/app/rosace/pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && find /opt/venv /usr/lib/python3/dist-packages -type d -name tests -not -path "*/astropy/tests" -not -path "*/numpy/_core/tests" -prune -exec rm -rf {} + \
    && find /opt/venv /usr/lib/python3/dist-packages -name "*.pyc" -delete

# The replay dataset: its own layer, before the code, so a code change does
# not copy it again. solve-field reads its index tiles there.
COPY docker/replay_data /opt/rosace-demo/replay
RUN printf 'inparallel\nadd_path /opt/rosace-demo/replay/astrometry\nautoindex\n' > /etc/astrometry.cfg

# The application. Editable install, deliberately: pyproject.toml has no
# package_data for the non-Python files (conf/*.yaml, programs/*.yaml,
# data/*.csv, gui/bin/*) — a regular install would drop them. pip is then
# removed from the environment (not needed to run).
COPY rosace/rosace /app/rosace/rosace
RUN pip install --no-cache-dir --no-deps -e /app/rosace \
    && pip uninstall -y pip

COPY --from=gui-builder /gui/bin /app/rosace/rosace/gui/bin

COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

ENV PYTHONUNBUFFERED=1

# The demo's data (databases, log, observations): config_demo.yaml's
# base_data_dir. A named volume here keeps them across restarts.
VOLUME /root/.rosace-demo

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
