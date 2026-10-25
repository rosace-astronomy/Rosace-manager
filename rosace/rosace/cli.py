# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The `rosace` command (pyproject.toml's [project.scripts]) and its options;
`python -m rosace.main` is the same command, main.py calling main() first
thing when run as a script. main() takes --host/--port, run_cli() handles
every other option, then main() starts the server.

Kept out of main.py so the orchestrator knows nothing of the installation
tools behind these options (config and horizon-file validation, admin
bootstrap, sub-plugin listing, purge of simulated data).

Import order matters: some options choose which config.yaml gets loaded
(ROSACE_CONFIG), and rosace.config reads it once, when first imported. So
this module imports nothing but os/sys at module level, and every option
imports what it needs inside its own branch -- run_cli() must run before
anything imports rosace.config.
"""

import os
import sys

_HELP_TEXT = """\
Usage: rosace [config_path] [--demo-mode] [--host H] [--port N] [--check-config [config_path]] [--create-admin username] [--list-subplugins [family]] [--purge-simulated [--apply]] [--purge-demo [--apply]] [--build-validation-data [--apply]] [--version]

  (`python -m rosace.main` is the same command, with the same options.)

  (no arguments)            Start the server using the active config, resolved in
                            priority order: ROSACE_CONFIG env var > <base_data_dir>/
                            conf/config.yaml (this installation's own config) > the
                            packaged conf/config.yaml.

  config_path               Start the server against this config.yaml instead of
                            the one that would normally be resolved — e.g. to run
                            a second instrument's config for this launch only.
                            One-off equivalent of setting ROSACE_CONFIG yourself.

  --demo-mode               Start the server against the packaged demo config
                            (conf/config_demo.yaml): the real 72ED programs run
                            against replay devices that hand back the frames of a
                            real night (no INDI server, no PHD2, no telescope),
                            isolated under ~/.rosace-demo. Needs the replay
                            dataset (scripts/build_demo_dataset.py --output
                            ~/.rosace-demo/replay) and solve-field. The Docker
                            image runs the same, dataset included.

  --host H                  Address the server listens on. Default 0.0.0.0: every
                            address of this PC, so the GUI is reachable from the
                            local network; 127.0.0.1: this PC only.

  --port N                  Port the server listens on (default 8000) — e.g.
                            `rosace --demo-mode --port 8001` beside a real server.

  --check-config [config_path]
                            Validate a config and exit — never starts the server.
                            With no path, validates whatever would normally be
                            resolved; with a path, validates that file instead.
                            If the config declares horizon_file, also parses and
                            validates that local-horizon file (prints its point
                            count). Prints [OK]/[FAIL] and exits 0/1.

  --create-admin username  Bootstrap the very first admin account and exit — never
                            starts the server. Prompts for the password (never
                            passed as an argument, to keep it out of shell history
                            and `ps`). Refuses if any account already exists: past
                            this first bootstrap, account creation goes through the
                            API (admin-only), not shell access to the machine.

  --list-subplugins [family]
                            List every sub-plugin found on disk (sequencer_plugins/,
                            reduction_plugins/, sharing_plugins/), one per line, with
                            its own one-line description (its module docstring's
                            first line) -- for discovering what already exists before
                            writing a new sub-plugin or duplicating a close one.
                            Optionally restrict to one family: sequencer, reduction,
                            or sharing. Never starts the server.

  --purge-simulated [--apply]
                            Remove the data of the sessions run with simulated
                            equipment (sessions.simulated = 1 in rosace.db: INDI
                            simulators, internal backend) — their database rows and
                            the files they wrote in their session directory. Without
                            --apply, only lists what would go. Never starts the server.

  --purge-demo [--apply]    Empty the demo's data directory (~/.rosace-demo:
                            databases, log, observations, previews...) to start a
                            fresh demo, keeping only the replay dataset. Refused
                            while a demo server runs. Without --apply, only lists
                            what would go. Never starts the server.

  --build-validation-data [--apply]
                            (Re)write the BeSS validation page's data (JSON and
                            plots, next to each final spectrum) of every spectrum
                            in the validation list, from its observation file --
                            for spectra reduced before the bess_validation step
                            existed. Never touches the database. Refused while a
                            session runs. Without --apply, only checks each
                            observation file can be read. Never starts the server.

  --version                 Print Rosace's version and exit.

  --help, -h                Show this message and exit.
"""

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def main(argv: list[str] | None = None) -> None:
    """Entry point of the `rosace` command: handles the options, then starts
    the server (unless an option exited first)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    host = _take_option(argv, "--host") or DEFAULT_HOST
    port_text = _take_option(argv, "--port")
    port = DEFAULT_PORT
    if port_text is not None:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            print(f"[FAIL] --port needs a number between 1 and 65535, got {port_text!r}")
            sys.exit(1)
        port = int(port_text)
    # Taken out before run_cli(), whose positional config path is argv[0].
    run_cli(argv)
    import uvicorn
    uvicorn.run("rosace.main:app", host=host, port=port)


def _take_option(argv: list[str], name: str) -> str | None:
    """Removes `name value` from argv and returns value (None when absent)."""
    if name not in argv:
        return None
    idx = argv.index(name)
    if idx + 1 >= len(argv):
        print(f"[FAIL] {name} needs a value")
        sys.exit(1)
    value = argv[idx + 1]
    del argv[idx:idx + 2]
    return value


def run_cli(argv: list[str]) -> None:
    """Handles argv (sys.argv[1:]). The tool options (--help, --check-config,
    --create-admin, --list-subplugins, --purge-simulated, --purge-demo,
    --build-validation-data, --version) exit the process;
    --demo-mode, a config path or no argument at all set ROSACE_CONFIG where
    needed and return, so main() goes on to start the server."""
    if "--help" in argv or "-h" in argv:
        print(_HELP_TEXT)
        sys.exit(0)
    if "--version" in argv:
        from importlib import metadata
        try:
            version = metadata.version("rosace")
        except metadata.PackageNotFoundError:
            version = "unknown"
        print(f"Rosace {version}")
        sys.exit(0)
    if "--check-config" in argv:
        idx = argv.index("--check-config")
        explicit_config = argv[idx + 1] if idx + 1 < len(argv) else None
        if explicit_config:
            os.environ["ROSACE_CONFIG"] = explicit_config
        try:
            import rosace.config as checked_config
        except Exception as e:
            print(f"[FAIL] config invalid: {e}")
            sys.exit(1)
        print(f"[OK] config valid: {checked_config._CONFIG_PATH}")
        if checked_config.HORIZON_FILE is not None:
            try:
                from rosace.horizon import load_horizon
                horizon_points = load_horizon(checked_config.HORIZON_FILE)
            except Exception as e:
                print(f"[FAIL] horizon file invalid: {e}")
                sys.exit(1)
            print(f"[OK] horizon file valid: {checked_config.HORIZON_FILE} ({len(horizon_points)} points)")
        try:
            # Importing the plugin registry instantiates the active scheduler
            # (load_scheduler()) and validates the sub-plugin catalogue.
            import rosace.plugins  # noqa: F401
        except Exception as e:
            print(f"[FAIL] plugins invalid: {e}")
            sys.exit(1)
        print(f"[OK] plugins valid (active scheduler: {checked_config.ACTIVE_SCHEDULER})")
        sys.exit(0)
    elif "--create-admin" in argv:
        idx = argv.index("--create-admin")
        username = argv[idx + 1] if idx + 1 < len(argv) else None
        if not username:
            print("[FAIL] --create-admin requires a username")
            sys.exit(1)
        import asyncio
        import getpass
        password = getpass.getpass("Password: ")

        async def _bootstrap():
            from rosace.auth import auth_db_init, ensure_first_admin
            await auth_db_init()
            await ensure_first_admin(username, password)

        try:
            asyncio.run(_bootstrap())
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        print(f"[OK] admin '{username}' created")
        sys.exit(0)
    elif "--list-subplugins" in argv:
        idx = argv.index("--list-subplugins")
        family = argv[idx + 1] if idx + 1 < len(argv) and not argv[idx + 1].startswith("-") else None
        from rosace.plugins.pipeline_loader import _SUBPLUGIN_FAMILY_DIRS, list_subplugins
        if family is not None and family not in _SUBPLUGIN_FAMILY_DIRS:
            print(f"[FAIL] unknown family {family!r} (expected one of: {', '.join(_SUBPLUGIN_FAMILY_DIRS)})")
            sys.exit(1)
        rows = list_subplugins([family] if family else None)
        for row_family, name, summary in rows:
            print(f"{row_family:10s} {name:20s} {summary}")
        sys.exit(0)
    elif "--purge-demo" in argv:
        apply = "--apply" in argv
        os.environ["ROSACE_CONFIG"] = os.path.join(os.path.dirname(__file__), "conf", "config_demo.yaml")
        from rosace.demo_purge import purge_demo, report
        try:
            entries, kept = purge_demo(apply=apply)
        except RuntimeError as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        if not entries:
            print("[OK] the demo has no data to purge")
            sys.exit(0)
        print(report(entries))
        print(f"Kept: {kept} (replay dataset)")
        print("[OK] purged -- the next demo start seeds a fresh demo" if apply
              else "Dry run: nothing deleted (add --apply to delete)")
        sys.exit(0)
    elif "--build-validation-data" in argv:
        apply = "--apply" in argv
        from rosace.plugins.reduction_plugins.bess_validation.bess_validation import rebuild_validation_data
        try:
            results = rebuild_validation_data(apply=apply)
        except RuntimeError as error:
            print(f"[FAIL] {error}")
            sys.exit(1)
        for obs_id, star, outcome in results:
            print(f"{obs_id}  {star:<16}  {outcome}")
        failed = sum(outcome.startswith("FAILED") for _, _, outcome in results)
        print(f"{len(results)} spectra, {failed} failed")
        if not apply:
            print("Dry run: nothing written (add --apply to write)")
        sys.exit(1 if failed else 0)
    elif "--purge-simulated" in argv:
        apply = "--apply" in argv
        from rosace.simulated_data import purge_simulated, report
        purges = purge_simulated(apply=apply)
        if not purges:
            print("[OK] no simulated session to purge")
            sys.exit(0)
        print(report(purges))
        print("[OK] purged" if apply else "Dry run: nothing deleted (add --apply to delete)")
        sys.exit(0)
    elif "--demo-mode" in argv:
        # Pure sugar for ROSACE_CONFIG: resolves the packaged demo config's
        # path (this module sits next to main.py, so its conf/ is the same)
        # and returns to main.py's normal server startup, same as the bare
        # config_path branch does. Actual demo behavior (seeding, fake PHD2,
        # isolation) lives entirely in conf/config_demo.yaml, demo_seed.py and
        # hardware_replay/, triggered by IS_DEMO_MODE — not by this flag itself.
        os.environ["ROSACE_CONFIG"] = os.path.join(os.path.dirname(__file__), "conf", "config_demo.yaml")
    elif argv:
        os.environ["ROSACE_CONFIG"] = argv[0]
