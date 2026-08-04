#!/bin/sh
set -e

# Demo-only step: populate targets.db with an illustrative catalogue the
# first time /data is empty (see seed_demo_data.py). No-op on every
# subsequent start, so it's safe to leave in the normal startup path.
python /app/docker/seed_demo_data.py

exec python -m rosace.main
