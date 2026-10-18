# Rosace — Docker demo

This folder, with the `Dockerfile` at the repository root, builds a
**demo** of Rosace that runs in a container: the real Be stars process —
pointing and plate solving, PHD2 guiding, science and calibration exposures,
the whole reduction down to the final spectrum and its summary PDF — on
**replayed hardware**. Instead of driving a telescope, the devices hand back
the frames recorded during a real night (9 observations of 2026-10-14,
M 57 included), and a fake PHD2 replays that night's guiding. Each exposure
lasts a few seconds on screen. This is **not** how a real observatory
installation is meant to run — it lets someone try Rosace with nothing
installed but Docker.

The demo is the same as `python -m rosace.main --demo-mode` outside Docker
(see `CLAUDE.md`, "Trying Rosace without hardware").

## Anonymized

Nothing in the image names the observer or the site: the frames' headers
give the site's position rounded to the degree (45° N, 6° E), the observer
is `rosace`, the site `Rosace-Demo`; the real installation's configuration
and hardware profiles, and the tests, are left out (`.dockerignore`).
`scripts/build_demo_dataset.py` anonymizes the dataset it builds and refuses
one where a name or the exact position is left.

## Prerequisites

- Docker (Linux: the distribution's package; Windows / Mac: Docker Desktop).
- The replay dataset in `docker/replay_data/` (about 490 MB, not in git),
  built once from the observatory's own data:

  ```
  python scripts/build_demo_dataset.py --guidelog <PHD2 GuideLog of the night>
  ```

  An existing dataset can be anonymized again on its own:
  `python scripts/build_demo_dataset.py --anonymize-only`.

## Build the image

From the repository root:

```
docker build -t rosace-demo .
```

First build: a few minutes (Python and Node.js dependencies, the GUI,
astrometry.net, the dataset). Later builds after a code change only redo the
last layers.

## Run the container

```
docker run -d -p 8001:8000 -v rosace-demo-data:/root/.rosace-demo --name rosace-demo rosace-demo
```

- `-p 8001:8000`: open http://localhost:8001 (any free port on the left).
- `-v rosace-demo-data:/root/.rosace-demo`: the demo's databases,
  observations and log, kept when the container is removed.

Log in with `rosace` / `rosace` — fixed demo credentials: **never expose this
port on the Internet**. Click START: the scheduler offers the recorded stars
one after the other, about a minute each, then their reduction runs.

## Useful commands

| Action | Command |
|---|---|
| Live logs | `docker logs -f rosace-demo` |
| Stop / restart | `docker stop rosace-demo` / `docker start rosace-demo` |
| A fresh demo (the stars are observed once per day) | `docker exec rosace-demo python -m rosace.main --purge-demo --apply`, then `docker restart rosace-demo` |
| Remove the container (keeps the volume) | `docker rm -f rosace-demo` |
| Start from scratch (deletes the volume) | `docker volume rm rosace-demo-data` |

After a code change: `docker build -t rosace-demo .`, `docker rm -f
rosace-demo`, then the `docker run` above again.

## Sharing the image without a registry

```
docker save rosace-demo | gzip > rosace-demo.tar.gz     # on the building machine
docker load -i rosace-demo.tar.gz                       # on the other one
```

The image is built for the building machine's architecture (amd64 here); an
Apple Silicon Mac runs it through emulation, more slowly. Publishing it
(ghcr.io) is not done yet.

## Credits

- Plate solving: astrometry.net (Lang et al. 2010), Debian package.
- Index tiles of the 5200 series: built by astrometry.net from the Tycho-2
  and ESA Gaia DR2 catalogues (Gaia Collaboration 2018).
