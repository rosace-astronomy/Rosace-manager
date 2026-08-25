# Rosace — Docker demo module

This folder contains everything needed to run a **demo** of Rosace in a
Docker container: pre-built GUI, simulated backend
(`sequencer_backend: internal`), pre-filled star catalogue, admin account
created automatically. This is **not** how a real observatory installation
is meant to run (see `Dockerfile`) — it's purely to let someone try Rosace
in a few commands, without installing anything besides Docker.

## Prerequisites

Install Docker (once):
- **Linux**: `docker` via the distribution's package manager, or
  https://docs.docker.com/engine/install/
- **Windows / Mac**: Docker Desktop — https://www.docker.com/products/docker-desktop/

Check it works: `docker --version`

## Build the image

From the repo root (where the `Dockerfile` lives):

```
docker build -t rosace-demo .
```

- `-t rosace-demo`: name given to the image, to find it again later.
- `.`: build from the current directory.

First build: one to two minutes (downloading Python and Node.js
dependencies, compiling the GUI). Later builds, if the code hasn't
changed, reuse what Docker already has — much faster.

## Run the container

```
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

- `-d`: run in the background.
- `-p 8080:8000`: host port 8080 connected to the container's port 8000 —
  then open http://localhost:8080. If port 8080 is already taken on your
  machine, change the first number (e.g. `-p 8081:8000`).
- `-v rosace-data:/data`: named volume, where the star catalogue,
  accounts and observations are kept — **survives** stopping or removing
  the container. Without this `-v`, everything would be lost on every
  restart.
- `--name rosace-demo`: name given to the container, to refer to it
  easily in the commands below.

## Log in

http://localhost:8080 (or the port chosen above)

- Username: `rosace`
- Password: `rosace`

Demo credentials created automatically on first startup
(`docker/seed_demo_data.py`) — deliberately simple, never do this for a
real installation exposed on a network. A small star catalogue (seeded
from `docker/seed_demo_data.py`) is already ready: clicking START in the
interface directly launches a simulated observation session.

## Useful commands

| Action | Command |
|---|---|
| View live logs | `docker logs -f rosace-demo` |
| Stop the container | `docker stop rosace-demo` |
| Restart it (same data) | `docker start rosace-demo` |
| Remove it (keeps the volume's data) | `docker rm -f rosace-demo` |
| Start from scratch (also deletes the data) | `docker volume rm rosace-data` |

## Updating after a code change

`docker build` rebuilds the **image**, but doesn't touch the already
running **container** (which keeps running the old version). After every
change (Python or GUI/X4):

```
docker build -t rosace-demo .
docker rm -f rosace-demo
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

Since the `rosace-data` volume is kept, the data (catalogue, accounts,
observations) survives the update — only the application changes, like
reinstalling software while keeping its configuration folder.

## Problem: "port is already in use" / "container name already in use"

- **Port already in use** (e.g. a Rosace instance is already running
  locally on 8000): change the host port in `-p`, e.g. `-p 8081:8000`.
- **Container name already taken** (`Conflict. The container name
  "/rosace-demo" is already in use`): a container with that name already
  exists, probably from an earlier attempt.
  - To reuse it as-is: `docker start rosace-demo`
  - To start clean: `docker rm rosace-demo` then rerun the `docker run`
    command above.

## Sharing the image without a registry (e.g. testing on another PC/Mac)

Before any publication to a registry (Docker Hub, GitHub Container
Registry...), the image can be transferred as a plain file — USB stick,
email, shared drive — with no Internet connection needed at transfer
time.

On the machine that built the image:

```
docker save rosace-demo | gzip > rosace-demo.tar.gz
```

Produces a `.tar.gz` file of about 700 MB.

On the machine receiving the file (Docker Desktop installed, nothing
else):

```
docker load -i rosace-demo.tar.gz
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

`docker load` re-imports the image under the same name (`rosace-demo`),
then it starts up exactly like on the original machine.

**Cross-platform nuance**: the image is built for the architecture of
the machine that ran `docker build` (typically `amd64`/Intel). On an
Apple Silicon Mac (M1/M2/M3...), Docker Desktop still runs it via a
built-in emulation mode — slower, but functional with no configuration
needed. For a native (faster) image on Apple Silicon, either rebuild it
directly on that Mac, or use `docker buildx` to produce a
multi-architecture image in one go (a refinement for later, not needed
for a first test).

## Future publication (not yet done)

Since the repo is on GitHub (`rosace-astronomy/Rosace-manager`), GitHub
Container Registry (`ghcr.io`) would be the natural option for public
distribution: free for a public repo, tied to the existing GitHub
account. Once published, anyone could directly run:

```
docker pull ghcr.io/rosace-astronomy/rosace-demo:latest
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo ghcr.io/rosace-astronomy/rosace-demo:latest
```

— no `git clone`, no local build. Could be automated with a GitHub
Action that rebuilds and republishes the image on every push. Not done
yet, by explicit decision (see discussion).
