"""
Orchestrator-level exceptions that any plugin may raise. Kept in a module of
its own, with no imports, so that both main.py and the plugin packages can
import it without ever creating a circular import.
"""


class SessionFatalError(Exception):
    """
    Unrecoverable failure — typically a hardware fault that a recovery attempt
    already failed to clear (e.g. a guiding camera that no longer answers).
    main.py's session_runner() stops the session on this exception instead of
    restarting from SCHEDULER as it would for an ordinary exception, since
    retrying can't fix it. Any plugin may raise it, but only after trying to
    recover: an error that a retry could still clear must stay an ordinary
    exception.
    """
