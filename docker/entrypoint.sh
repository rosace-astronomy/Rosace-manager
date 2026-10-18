#!/bin/sh
set -e

# The demo reads its replay dataset from ~/.rosace-demo/replay
# (conf/hardware_replay.yaml): a link to the copy in the image, made once in
# the data volume (--purge-demo keeps it).
mkdir -p "$HOME/.rosace-demo"
[ -e "$HOME/.rosace-demo/replay" ] || ln -s /opt/rosace-demo/replay "$HOME/.rosace-demo/replay"

exec python -m rosace.main --demo-mode
