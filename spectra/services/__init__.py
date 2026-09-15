"""Services — thin orchestrators, the one API both the CLI and the UI call (doc 3 §2).

Each operation follows the same shape: validate → audit `*.start` → do the work → audit
`*.complete` / `*.error` → rewrite the case manifest.
"""


class ServiceError(Exception):
    """A request the service refuses, with a message fit to show the examiner."""
