# Welcome to the ROSACE project[](https://rosace-astronomy.github.io/rosace-docs/#welcome-to-the-rosace-project)

ROSACE is the acronym for **Robotic Observations and Spectroscopic Astronomy, a Collaborative Experience**

The ROSACE project aims to develop a network of amateur and  professional observatories to run Robotic Spectroscopic astronomical  observations. All together, we offer a new tool to the Science to better understand  our Universe.

The project is based on Open Source software tools only.

Robotic observation means that **no human action** is required. From opening the dome (or shelter) to processing the data and sharing the result, all is automated.

The project is developed by the community: your contribution is welcome. Our intention is to propose a system that can be adapted from smallest to biggest telescopes.

## Quick start (development)

The fastest way to try Rosace is the [Docker demo](docker/README.md) — no
Python setup needed. To run it from source instead:

```bash
# Activate the virtual environment
source rosace-manager/bin/activate

# Install in editable mode with dev dependencies (from the repo root)
pip install -e "rosace/[dev]"

# First run only: bootstrap the first admin account
python -m rosace.main --create-admin <username>

# Start the server
cd rosace
uvicorn rosace.main:app --host 0.0.0.0 --port 8000
# or: python -m rosace.main
```

The web GUI is served at http://localhost:8000/, and the interactive API
docs at http://localhost:8000/docs.

Run the test suite:

```bash
cd rosace
pytest rosace -v
```

See `CLAUDE.md` for the full architecture reference.

Contents:

- [Introduction](https://rosace-astronomy.github.io/rosace-docs/intro.html)
- [Why Robotic Spectroscopy](https://rosace-astronomy.github.io/rosace-docs/Why_Robotic_Spectroscopy.html)
- [What does (and what does not) ROSACE software?](https://rosace-astronomy.github.io/rosace-docs/What_does_ROSACE.html)
- [ROSACE at a glance](https://rosace-astronomy.github.io/rosace-docs/ROSACE_at_glance.html)
- [Technical choices](https://rosace-astronomy.github.io/rosace-docs/Technical_choices.html)
- [Get started](https://rosace-astronomy.github.io/rosace-docs/GetStarted.html)
- [How to Contribute](https://rosace-astronomy.github.io/rosace-docs/Contribute.html)
- [Step 1: session initialization](https://rosace-astronomy.github.io/rosace-docs/Modules/Module1.html)
- [Step 2: Scheduler](https://rosace-astronomy.github.io/rosace-docs/Modules/Module2.html)
- [Step 3: Sequencer](https://rosace-astronomy.github.io/rosace-docs/Modules/Module3.html)
- [Step 4: Data Reduction](https://rosace-astronomy.github.io/rosace-docs/Modules/Module4.html)
- [Step 5: Display, share](https://rosace-astronomy.github.io/rosace-docs/Modules/Module5.html)
- [Step 6: Archive](https://rosace-astronomy.github.io/rosace-docs/Modules/Module6.html)
- [Step 7: Closing the session](https://rosace-astronomy.github.io/rosace-docs/Modules/Module7.html)
- [Observing file format](https://rosace-astronomy.github.io/rosace-docs/ObsFileFormat.html)

Please send any comment or question to François Cochard ([francois.cochard@shelyak.com](mailto:francois.cochard@shelyak.com))
