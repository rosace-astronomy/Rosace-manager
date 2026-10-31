# Welcome to the ROSACE project[](https://rosace-astronomy.github.io/rosace-docs/#welcome-to-the-rosace-project)

ROSACE is the acronym for **Robotic Observations and Spectroscopic Astronomy, a Collaborative Experience**

The ROSACE project aims to develop a network of amateur and  professional observatories to run Robotic Spectroscopic astronomical  observations. All together, we offer a new tool to the Science to better understand  our Universe.

The project is based on Open Source software tools only.

Robotic observation means that **no human action** is required during the night: from choosing the next target to processing the data and sharing the result, all is automated. Deciding when to observe and protecting the instrument (Sun, weather, roof or dome) is left to the observatory's own system, which starts and stops ROSACE.

The project is developed by the community: your contribution is welcome. Our intention is to propose a system that can be adapted from smallest to biggest telescopes.

## Get started

The user documentation explains, step by step, how to start with ROSACE:
[Get started](https://rosace-astronomy.github.io/rosace-docs/GetStarted.html)
— step 1, the Docker demo (nothing to install but Docker); step 2, ROSACE
installed on your computer, running the same demo (Python environment, demo
data, astrometry.net, optional `rosace` command and Tab completion).

## Quick start (development)

To work on the code, install ROSACE as in
[Get started, step 2](https://rosace-astronomy.github.io/rosace-docs/GetStarted.html#step-2-install-rosace-on-your-computer-and-run-the-same-demo),
with the development tools (from the repo root, the environment active):

```bash
pip install -e "rosace/[dev]"
```

To run ROSACE outside the demo, create the first admin account once
(`rosace --create-admin <username>`), then start the server with `rosace`
(`rosace --help` lists the options).

The web GUI comes built in `rosace/rosace/gui/bin/`. After changing its
sources (`rosace/rosace/gui/src/`), rebuild it (Node.js needed) and commit
`gui/bin/` with them:

```bash
cd rosace/rosace/gui && npm install && npm run build
```

The web GUI is served at http://localhost:8000/, and the interactive API
docs at http://localhost:8000/docs.

## Run the tests

ROSACE relies heavily on automated tests: More than 1 500 (Oct. 2026) of them check every
part of the code — the orchestrator, the schedulers, each acquisition and
reduction step, the databases, the API — on simulated devices and synthetic
images, with no telescope needed. Every change is expected to keep them all
passing, and new code comes with its own tests.

The tests need the development tools (pytest and its plugins), which a plain
installation does not include. Install them once, from the repo root with the
environment active — `[dev]` is the group of these tools, declared in
`rosace/pyproject.toml`:

```bash
pip install -e "rosace/[dev]"
```

Then run the tests, from the repo root:

```bash
python -m pytest rosace/rosace
```

`python -m pytest` makes sure the environment's pytest is used, not one
installed on the system (which does not see ROSACE's libraries and fails with
`ModuleNotFoundError`). The whole suite takes a few minutes; add `-v` to list
each test, or give one file, e.g.
`python -m pytest rosace/rosace/test_rosace_core.py`.

See `CLAUDE.md` for the full architecture reference, and
[CHANGELOG.md](CHANGELOG.md) for what changed in each published version.

Contents:

- [Introduction](https://rosace-astronomy.github.io/rosace-docs/intro.html)
- [Why Robotic Spectroscopy](https://rosace-astronomy.github.io/rosace-docs/Why_Robotic_Spectroscopy.html)
- [What does (and what does not) ROSACE software?](https://rosace-astronomy.github.io/rosace-docs/What_does_ROSACE.html)
- [ROSACE at a glance](https://rosace-astronomy.github.io/rosace-docs/ROSACE_at_glance.html)
- [Technical choices](https://rosace-astronomy.github.io/rosace-docs/Technical_choices.html)
- [Get started](https://rosace-astronomy.github.io/rosace-docs/GetStarted.html)
- [Adapt ROSACE to your observatory, and contribute](https://rosace-astronomy.github.io/rosace-docs/Contribute.html)
- [Step 1: session initialization](https://rosace-astronomy.github.io/rosace-docs/Modules/Module1.html)
- [Step 2: Scheduler](https://rosace-astronomy.github.io/rosace-docs/Modules/Module2.html)
- [Step 3: Sequencer](https://rosace-astronomy.github.io/rosace-docs/Modules/Module3.html)
- [Step 4: Data Reduction](https://rosace-astronomy.github.io/rosace-docs/Modules/Module4.html)
- [Step 5: Display, share](https://rosace-astronomy.github.io/rosace-docs/Modules/Module5.html)
- [Step 6: Archive](https://rosace-astronomy.github.io/rosace-docs/Modules/Module6.html)
- [Step 7: Closing the session](https://rosace-astronomy.github.io/rosace-docs/Modules/Module7.html)
- [Observing file format](https://rosace-astronomy.github.io/rosace-docs/ObsFileFormat.html)

Please send any comment or question to François Cochard ([francois.cochard@shelyak.com](mailto:francois.cochard@shelyak.com))

## License

Rosace is licensed under the GNU General Public License v3.0 or later
(SPDX: `GPL-3.0-or-later`) — see [LICENSE](LICENSE) for the full text.

Some files are adapted from third-party projects and remain under those
projects' own license — see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

### In plain words

- **Use, modification, distribution**: the software may be freely used, studied, modified, and distributed.
- **Copyleft**: any modified version that is distributed must also be released under the GPL, with its source code made available.
- **Attribution**: redistributed copies must keep the license text and existing copyright notices intact.
- **Patent protection**: GPLv3 includes an explicit patent grant and protection against patent retaliation.
- **No warranty, limited liability**: the software is provided "as is", with no warranty, and contributors' liability is limited.

This is an informal summary for convenience only — it does not replace, and cannot override, the full license text in [LICENSE](LICENSE).
