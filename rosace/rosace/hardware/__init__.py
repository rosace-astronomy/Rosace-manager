"""
Generic, backend-agnostic device interfaces (Mount, Camera, Focuser, FilterWheel).

Each interface is a typing.Protocol: a structural contract, not tied to any
one backend. hardware_indi/ (INDI, today) and a future hardware_ascom/
(Ascom/Alpaca) each provide their own concrete classes satisfying these
contracts — SEQUENCER sub-plugins only ever depend on the interfaces here,
never on a specific backend's classes.

Method names follow ASCOM's own published device interfaces
(https://ascom-standards.org/newdocs/) for naming consistency only — not
for API completeness: each interface here declares only what Rosace
actually needs today.
"""
