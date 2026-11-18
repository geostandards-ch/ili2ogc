"""CLI presentation: process exit codes and colored stderr messages.

`ExitCode` mirrors `DiagnosticBag.exit_code()` (0/1/2) and adds 3-5 for hard stops before any bag exists.
"""

from __future__ import annotations

import os
import sys
from enum import IntEnum
from typing import TextIO


class ExitCode(IntEnum):
    """Process exit codes for `interlis.cli.main`; 0/1/2 are `DiagnosticBag.exit_code()`'s values, never repurposed."""

    OK = 0
    ERROR = 1  # DiagnosticBag: an `error` diagnostic, or --strict with any diagnostic
    DEGRADED = 2  # DiagnosticBag: completed, only note/warning diagnostics
    USAGE = 3  # invocation is missing what it needs (e.g. no --model and no --repo to auto-detect one)
    NOT_FOUND = 4  # a named file, or a resource looked up in --repo/--catalog/TRANSLATION, doesn't exist
    INVALID = 5  # an input file exists but fails to parse (.ili/.xtf syntax errors)


_PALETTE = {"error": "31", "warning": "33"}


def use_color(stream: TextIO) -> bool:
    """`NO_COLOR` always wins; otherwise color only when `stream` is a real terminal."""
    return "NO_COLOR" not in os.environ and stream.isatty()


def _paint(text: str, code: str, stream: TextIO) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color(stream) else text


def error(message: str, *, stream: TextIO | None = None) -> None:
    """Print `message` to `stream` (default `sys.stderr`) prefixed `error:` (red on a real terminal).

    `stream` is resolved inside the body because a default value would be bound at import time and miss a later
    `sys.stderr` swap.
    """
    stream = stream if stream is not None else sys.stderr
    print(f"{_paint('error', _PALETTE['error'], stream)}: {message}", file=stream)


def warn(message: str, *, stream: TextIO | None = None) -> None:
    """Print `message` to `stream` (default `sys.stderr`) prefixed `warning:` (yellow on a real terminal)."""
    stream = stream if stream is not None else sys.stderr
    print(f"{_paint('warning', _PALETTE['warning'], stream)}: {message}", file=stream)
