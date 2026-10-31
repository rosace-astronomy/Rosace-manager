# Rosace — Docker demo

This folder, with the `Dockerfile` at the repository root, builds a
**demo** of Rosace that runs in a container: the real Be stars process —
pointing and plate solving, PHD2 guiding, science and calibration exposures,
the whole reduction down to the final spectrum and its summary PDF — on
**replayed hardware**. Instead of driving a telescope, the devices hand back
the frames recorded during a real night (4 observations of 2026-10-14:
psi Per, eps Cas, M 57, eta Ori), and a fake PHD2 replays that night's
guiding. It lets someone try Rosace with nothing installed but Docker; it
is not how a real observatory installation runs.

## To try the demo

Use the published image, as described in the user documentation:

**<https://rosace-astronomy.github.io/rosace-docs/GetStarted.html#step-1-try-the-docker-demo>**

(how to start the demo, use it, run it again, and what to do when something
goes wrong). The whole user documentation:
<https://rosace-astronomy.github.io/rosace-docs/>.

## For whoever builds or publishes the image

The sections below are for them only. The demo itself is the same as
`rosace --demo-mode` outside Docker (see the developer notes in
`CLAUDE.md`, "Trying Rosace without hardware").

## Anonymized

Nothing in the image names the observer or the site: the frames' headers
give the site's position rounded to the degree (45° N, 6° E) and no longer
hold the target's altitude, azimuth or airmass (which would give the
position back), the observer is `rosace`, the site `Rosace-Demo`. The real
installation's configuration lives outside the repository, and the tests are
left out of the image (`.dockerignore`). `scripts/build_demo_dataset.py`
anonymizes the dataset it builds and refuses one where a name or the exact
position is left.

## The replay dataset

The image needs the replay dataset in `docker/replay_data/` (about 120 MB,
frames gzip-compressed). It is **not in git**: without it, `docker build`
stops with "the demo's replay dataset is missing". It is published as a
file of the release `demo-data-2026-10-14`, on Codeberg and on its GitHub
mirror (the same file):

- <https://codeberg.org/FrancoisCochard/Rosace-manager/releases/download/demo-data-2026-10-14/rosace-demo-data-2026-10-14.tar>
- <https://github.com/rosace-astronomy/Rosace-manager/releases/download/demo-data-2026-10-14/rosace-demo-data-2026-10-14.tar>

From the repository root:

```
sha256sum rosace-demo-data-2026-10-14.tar
tar -xf rosace-demo-data-2026-10-14.tar -C docker/
```

The checksum must read
`623405982b7c9653676ee0f9c2e296ec472cc10f9dc654b1e63f4ba344801501` (also
given by the release's `.sha256` file); `tar` creates `docker/replay_data/`.

The dataset is built by the observatory from its own data, then published
as a new release (never a change to an existing one):

```
python scripts/build_demo_dataset.py --guidelog <PHD2 GuideLog of the night>
```

An existing dataset can be anonymized again on its own:
`python scripts/build_demo_dataset.py --anonymize-only`.

## Build and run the image

From the repository root:

```
docker build -t rosace-demo .
docker run -d -p 8001:8000 -v rosace-demo-data:/root/.rosace-demo --name rosace-demo rosace-demo
```

First build: a few minutes (Python and Node.js dependencies, the GUI,
astrometry.net, the dataset). Later builds after a code change only redo the
last layers; then `docker rm -f rosace-demo` and the `docker run` above
again (the volume keeps the demo's data).

The image is about 340 MB to download, 1.1 GB once installed: it runs on
Debian's own Python, which astrometry.net needs anyway. It is built for the
building machine's architecture (amd64 here); an Apple Silicon Mac runs it
through emulation, more slowly.

Maintenance commands, besides those of the user documentation:

| Action | Command |
|---|---|
| Remove a locally built image (after removing the container) | `docker rmi rosace-demo` |
| Reclaim the space of old builds (unnamed images, asks first) | `docker image prune` |

## Sharing the image without a registry

```
docker save rosace-demo | gzip > rosace-demo.tar.gz     # on the building machine
docker load -i rosace-demo.tar.gz                       # on the other one
```

## Publishing a version (maintainer)

Only published versions (X.Y.Z.0, see `CLAUDE.md`, "Versioning") go to the
registry, built from their git tag on a clean tree, with the dataset in
place. Log in once with a Codeberg access token (Settings → Applications,
permission `package` read and write only):

```
docker login codeberg.org -u FrancoisCochard
docker build -t rosace-demo:<version> .
docker tag rosace-demo:<version> codeberg.org/francoiscochard/rosace-demo:<version>
docker tag rosace-demo:<version> codeberg.org/francoiscochard/rosace-demo:latest
docker push codeberg.org/francoiscochard/rosace-demo:<version>
docker push codeberg.org/francoiscochard/rosace-demo:latest
```

The image name is lower case (Docker refuses capitals); the package is
attached to the Codeberg account, and public because the account is.

## Credits

- Plate solving: astrometry.net (Lang et al. 2010), Debian package.
- Index tiles of the 5200 series: built by astrometry.net from the Tycho-2
  and ESA Gaia DR2 catalogues (Gaia Collaboration 2018).
