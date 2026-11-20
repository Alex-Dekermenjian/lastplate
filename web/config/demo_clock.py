"""Demo clock (development only).

When DEMO_CLOCK_FILE is set, "now" is the real time plus an offset that is
read from that file. The web site's clock box writes the file; every service
(and the timers container) reads it, so the pages, pickup windows, order
expiry and timers all agree on the same moved time.

Only the business time changes (django.utils.timezone.now). Sign-in tokens
(JWT) keep using the real time, so moving the clock never logs anyone out
because of a token. Tests never use it. With DEMO_CLOCK_FILE unset (CI,
production) nothing changes at all.
"""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

# About ten years either way is plenty for a demo, and keeps dates valid.
MAX_OFFSET_SECONDS = 10 * 366 * 24 * 3600

_real_now = timezone.now
_seen = {"mtime": None, "offset": 0}


def path():
    return os.environ.get("DEMO_CLOCK_FILE") or None


def testing():
    """Tests and makemigrations must see Django's own clock (model defaults)."""
    return "test" in sys.argv or "makemigrations" in sys.argv


def real_now():
    return _real_now()


def offset_seconds():
    """The moved-by seconds, 0 when there is no file or it cannot be read."""
    file = path()
    if not file:
        return 0
    try:
        stat = os.stat(file)
    except OSError:
        _seen.update(mtime=None, offset=0)
        return 0
    mtime = (stat.st_mtime_ns, stat.st_size, stat.st_ino)  # a new write changes at least one
    if mtime != _seen["mtime"]:
        try:
            value = json.loads(Path(file).read_text(encoding="utf-8"))["offset_seconds"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("offset_seconds must be a number")
            offset = max(-MAX_OFFSET_SECONDS, min(MAX_OFFSET_SECONDS, int(value)))
        except (OSError, ValueError, KeyError, TypeError):
            offset = 0  # a half-written or broken file means the real time
        _seen.update(mtime=mtime, offset=offset)
    return _seen["offset"]


def demo_now():
    return _real_now() + timedelta(seconds=offset_seconds())


demo_now.is_demo_clock = True


def install():
    """Make django.utils.timezone.now() the demo clock. Safe to call twice."""
    if path() and not testing() and not getattr(timezone.now, "is_demo_clock", False):
        timezone.now = demo_now


# ---- web only: the site is the one place that moves the clock ----

def enabled():
    """True when this run has a clock file to write (never while testing)."""
    return bool(path()) and not testing()


def set_offset(seconds):
    """Write the offset atomically so another service never reads half a file."""
    seconds = max(-MAX_OFFSET_SECONDS, min(MAX_OFFSET_SECONDS, int(seconds)))
    file = Path(path())
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_name(file.name + ".tmp")
    temp.write_text(json.dumps({"offset_seconds": seconds}), encoding="utf-8")
    os.replace(temp, file)
