"""The launcher's own log file, for bug reports: ~/.local/state/aacam/aacam.log, rotated at 1 MB."""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

from . import __version__
from .config import state_dir

# Tokens never reach the log, even if a URL or an error message carries one.
SECRET_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+\S+)")


def path() -> Path:
    return state_dir() / "aacam.log"


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = SECRET_RE.sub("[hidden]", record.getMessage())
        record.args = None
        return True


def setup(program: str) -> None:
    """Log to the file from here on; safe to call more than once."""
    root = logging.getLogger("aacam")
    for h in [h for h in root.handlers if getattr(h, "_aacam", False)]:
        if Path(h.baseFilename) == path():
            return
        root.removeHandler(h)  # the state folder moved (tests, or XDG_STATE_HOME changed)
        h.close()
    try:
        path().parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path(), maxBytes=1 << 20, backupCount=2, encoding="utf-8")
    except OSError:
        return  # a read-only home must not stop the launcher
    handler._aacam = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_Redact())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.info("%s %s started", program, __version__)


def tail(lines: int = 80) -> str:
    try:
        text = path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return SECRET_RE.sub("[hidden]", "\n".join(text.splitlines()[-lines:]))
