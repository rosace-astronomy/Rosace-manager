# PYTHON_ARGCOMPLETE_OK
# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The `rosace` command (pyproject.toml's [project.scripts]) and its options;
`python -m rosace.main` is the same command, main.py calling main() first
thing when run as a script. build_parser() declares every option (argparse:
standard errors and exit code 2, `--option=value`, generated help, and the
Tab completion argcomplete reads from it); run_cli() runs a tool option or
prepares the server start, then main() starts the server.

Kept out of main.py so the orchestrator knows nothing of the installation
tools behind these options (config creation and validation, horizon-file
validation, admin bootstrap, sub-plugin listing, purge of simulated data and of failed
acquisitions).

Import order matters: some options choose which config.yaml gets loaded
(ROSACE_CONFIG), and rosace.config reads it once, when first imported. So
this module imports nothing but argparse/os/sys at module level, and every
option imports what it needs inside its own branch -- run_cli() must run
before anything imports rosace.config.
"""

import argparse
import os
import sys

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# The sub-plugin families (pipeline_loader._SUBPLUGIN_FAMILY_DIRS, not
# imported here: it would load the config).
SUBPLUGIN_FAMILIES = ("sequencer", "reduction", "sharing")

# The tool options: they run, then exit, and never start the server.
_TOOL_OPTIONS = ("check_config", "init_config", "create_admin", "list_subplugins",
                 "purge_simulated", "purge_failed", "purge_demo", "build_validation_data")
_APPLY_OPTIONS = ("purge_simulated", "purge_failed", "purge_demo", "build_validation_data")

_DESCRIPTION = """Start the Rosace server, or run one of the tool options below.

With no argument, the server uses the active config, resolved in priority
order: the ROSACE_CONFIG env var > ~/.config/rosace/config.yaml (this
installation's own config; the directory follows $XDG_CONFIG_HOME when set)
> the packaged conf/config.yaml (the INDI simulators). The hardware profile
named by active_hardware is looked for next to that config.yaml first, then
among the packaged profiles.

`python -m rosace.main` is the same command, with the same options."""

_EPILOG = (
    "Tab completion of the options: add this line to ~/.bashrc once (full path:\n"
    "the virtual environment is not active there), then open a new terminal:\n"
    '  eval "$(<venv>/bin/register-python-argcomplete rosace)"\n'
    "See README.md (using rosace without activating the environment)."
)


def _night(value: str) -> str:
    """argparse type of --purge-failed: a YYYY-MM-DD date."""
    from datetime import datetime
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: '{value}'")
    return value


def _version() -> str:
    from importlib import metadata
    try:
        return metadata.version("rosace")
    except metadata.PackageNotFoundError:
        return "unknown"


def _port(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise argparse.ArgumentTypeError(f"a number between 1 and 65535, got {text!r}")
    return int(text)


def build_parser() -> argparse.ArgumentParser:
    """Every option of the command — also what Tab completion offers."""
    parser = argparse.ArgumentParser(
        prog="rosace", description=_DESCRIPTION, epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_path = parser.add_argument(
        "config_path", nargs="?",
        help="start the server against this config.yaml instead of the one that "
             "would normally be resolved (e.g. a second instrument's, or the "
             "packaged simulators') -- one-off equivalent of setting ROSACE_CONFIG",
    )

    server = parser.add_argument_group("server")
    server.add_argument(
        "--demo-mode", action="store_true",
        help="start against the packaged demo config (conf/config_demo.yaml): the "
             "real 72ED programs run on replay devices handing back the frames of "
             "a real night (no INDI server, no PHD2, no telescope), isolated under "
             "~/.rosace-demo. Needs the replay dataset (scripts/build_demo_dataset.py "
             "--output ~/.rosace-demo/replay) and solve-field; the Docker image "
             "runs the same, dataset included",
    )
    server.add_argument(
        "--host", default=DEFAULT_HOST,
        help="address the server listens on (default %(default)s: every address "
             "of this PC, the GUI reachable from the local network; 127.0.0.1: "
             "this PC only)",
    )
    server.add_argument(
        "--port", type=_port, default=DEFAULT_PORT,
        help="port the server listens on (default %(default)s), e.g. "
             "`rosace --demo-mode --port 8001` beside a real server",
    )

    tools = parser.add_argument_group("tool options (one at a time; never start the server)")
    exclusive = tools.add_mutually_exclusive_group()
    check_config = exclusive.add_argument(
        "--check-config", nargs="?", const="", metavar="CONFIG_PATH",
        help="validate a config and exit: with no path, the one that would "
             "normally be resolved; with a path, that file. Also prints the "
             "hardware profile found and, when the config declares horizon_file, "
             "validates that local-horizon file. Prints [OK]/[FAIL], exits 0/1",
    )
    exclusive.add_argument(
        "--init-config", action="store_true",
        help="create ~/.config/rosace/ and copy the packaged config (INDI "
             "simulators) there as config.yaml, the starting point of this "
             "installation's own config. Refuses if a config.yaml is already there",
    )
    exclusive.add_argument(
        "--create-admin", metavar="USERNAME",
        help="bootstrap the very first admin account (password prompted, never "
             "an argument). Refuses if any account exists: past this bootstrap, "
             "accounts are created through the API (admin only)",
    )
    exclusive.add_argument(
        "--list-subplugins", nargs="?", const=True, choices=SUBPLUGIN_FAMILIES,
        metavar="FAMILY",
        help="list every sub-plugin on disk with its one-line description, "
             "optionally one family only: " + ", ".join(SUBPLUGIN_FAMILIES),
    )
    exclusive.add_argument(
        "--purge-simulated", action="store_true",
        help="remove the data of the sessions run with simulated equipment "
             "(INDI simulators, internal backend): their database rows and the "
             "files they wrote. Without --apply, only lists what would go",
    )
    exclusive.add_argument(
        "--purge-failed", type=_night, metavar="DATE",
        help="remove what the observations of one night (YYYY-MM-DD, the date "
             "of its session directory) whose acquisition failed left behind: "
             "their database rows and files; every other observation is kept. "
             "Refused while a session runs. Without --apply, only lists what would go",
    )
    exclusive.add_argument(
        "--purge-demo", action="store_true",
        help="empty the demo's data directory (~/.rosace-demo) to start a fresh "
             "demo, keeping the replay dataset. Refused while a demo server runs. "
             "Without --apply, only lists what would go",
    )
    exclusive.add_argument(
        "--build-validation-data", action="store_true",
        help="(re)write the BeSS validation page's data of every spectrum in the "
             "validation list, from its observation file. Never touches the "
             "database; refused while a session runs. Without --apply, only "
             "checks each observation file can be read",
    )
    tools.add_argument(
        "--apply", action="store_true",
        help="with --purge-simulated, --purge-failed, --purge-demo or --build-validation-data: "
             "really do it (otherwise a dry run)",
    )
    parser.add_argument("--version", action="version", version=f"Rosace {_version()}")

    try:
        from argcomplete.completers import FilesCompleter
    except ImportError:
        pass
    else:
        config_path.completer = FilesCompleter(["yaml"])
        check_config.completer = FilesCompleter(["yaml"])
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point of the `rosace` command: handles the options, then starts
    the server (unless a tool option exited first)."""
    parser = build_parser()
    try:
        import argcomplete
    except ImportError:
        pass  # no Tab completion, everything else works
    else:
        argcomplete.autocomplete(parser)
    args = run_cli(sys.argv[1:] if argv is None else argv, parser)
    import uvicorn
    uvicorn.run("rosace.main:app", host=args.host, port=args.port)


def run_cli(argv: list[str], parser: argparse.ArgumentParser | None = None) -> argparse.Namespace:
    """Parses argv (sys.argv[1:]). A tool option (--check-config, --init-config,
    --create-admin, --list-subplugins, --purge-simulated, --purge-failed, --purge-demo,
    --build-validation-data) runs and exits the process, as do --help,
    --version and a usage error (exit 2); otherwise --demo-mode or a config
    path set ROSACE_CONFIG where needed, and the parsed options are returned
    so main() goes on to start the server."""
    parser = parser or build_parser()
    args = parser.parse_args(argv)
    tool = next((name for name in _TOOL_OPTIONS
                 if getattr(args, name) not in (None, False)), None)
    if args.apply and tool not in _APPLY_OPTIONS:
        parser.error("--apply only goes with --purge-simulated, --purge-failed, --purge-demo "
                     "or --build-validation-data")
    if tool and args.config_path:
        parser.error(f"a config path cannot go with --{tool.replace('_', '-')}"
                     + (" (give it to --check-config itself)" if tool == "check_config" else ""))
    if tool and args.demo_mode:
        parser.error(f"--demo-mode cannot go with --{tool.replace('_', '-')}")
    if args.demo_mode and args.config_path:
        parser.error("--demo-mode and a config path cannot go together")

    if tool == "check_config":
        if args.check_config:
            os.environ["ROSACE_CONFIG"] = args.check_config
        try:
            import rosace.config as checked_config
        except Exception as e:
            print(f"[FAIL] config invalid: {e}")
            sys.exit(1)
        print(f"[OK] config valid: {checked_config._CONFIG_PATH}")
        print(f"[OK] hardware profile: {checked_config.HARDWARE_CONFIG_PATH}")
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
    elif tool == "init_config":
        # Loads the packaged config only to reach config.py's own paths, so a
        # broken config elsewhere can never stop the init.
        os.environ["ROSACE_CONFIG"] = os.path.join(os.path.dirname(__file__), "conf", "config.yaml")
        import shutil
        import rosace.config as packaged_config
        target = packaged_config._user_config_dir() / "config.yaml"
        if target.exists():
            print(f"[FAIL] {target} already exists -- nothing changed")
            sys.exit(1)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(packaged_config._DEFAULT_CONFIG_PATH, target)
        print(f"[OK] created {target} (a copy of the INDI simulators config)")
        print("Edit it for this installation (observatory block, active_hardware...);")
        print(f"a hardware profile of your own goes next to it, in {target.parent}.")
        print("Then check it: rosace --check-config")
        sys.exit(0)
    elif tool == "create_admin":
        username = args.create_admin
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
    elif tool == "list_subplugins":
        family = None if args.list_subplugins is True else args.list_subplugins
        from rosace.plugins.pipeline_loader import list_subplugins
        rows = list_subplugins([family] if family else None)
        for row_family, name, summary in rows:
            print(f"{row_family:10s} {name:20s} {summary}")
        sys.exit(0)
    elif tool == "purge_demo":
        apply = args.apply
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
    elif tool == "build_validation_data":
        apply = args.apply
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
    elif tool == "purge_simulated":
        apply = args.apply
        from rosace.simulated_data import purge_simulated, report
        purges = purge_simulated(apply=apply)
        if not purges:
            print("[OK] no simulated session to purge")
            sys.exit(0)
        print(report(purges))
        print("[OK] purged" if apply else "Dry run: nothing deleted (add --apply to delete)")
        sys.exit(0)
    elif tool == "purge_failed":
        apply = args.apply
        from rosace.failed_data import purge_failed, report
        try:
            purges = purge_failed(args.purge_failed, apply=apply)
        except RuntimeError as error:
            print(f"[FAIL] {error}")
            sys.exit(1)
        if not purges:
            print(f"[OK] nothing to purge for the night of {args.purge_failed}")
            sys.exit(0)
        print(report(purges))
        print("[OK] purged" if apply else "Dry run: nothing deleted (add --apply to delete)")
        sys.exit(0)
    elif args.demo_mode:
        # Pure sugar for ROSACE_CONFIG: resolves the packaged demo config's
        # path (this module sits next to main.py, so its conf/ is the same)
        # and returns to main.py's normal server startup, same as the bare
        # config_path branch does. Actual demo behavior (seeding, fake PHD2,
        # isolation) lives entirely in conf/config_demo.yaml, demo_seed.py and
        # hardware_replay/, triggered by IS_DEMO_MODE — not by this flag itself.
        os.environ["ROSACE_CONFIG"] = os.path.join(os.path.dirname(__file__), "conf", "config_demo.yaml")
    elif args.config_path:
        os.environ["ROSACE_CONFIG"] = args.config_path
    return args
