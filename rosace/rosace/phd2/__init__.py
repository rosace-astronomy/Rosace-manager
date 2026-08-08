"""
PHD2 guiding client — Phd2Client (phd2_client.py) is the whole package: a
single implementation, backend-agnostic. Unlike the INDI/Ascom device axis
(hardware/, hardware_indi/), PHD2 is reached the same way (its own TCP
JSON-RPC protocol, port 4400) no matter which telescope/camera backend is
active underneath it — there is nothing to abstract over, so no Protocol
interface and no per-backend adapter exist here.
"""
