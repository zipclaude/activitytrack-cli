# SPDX-License-Identifier: GPL-3.0-or-later
"""
The terminal: colours when it supports them (not when redirected, nor with
NO_COLOR; on Windows once virtual-terminal sequences are on), and the
choice from a numbered list of the interactive mode.
"""

import os
import shutil
import sys

from activitytrack.i18n import _

_codes = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35",
    "cyan": "36", "grey": "90",
}
# the app's colours of the modes and states
MODE_COLOURS = {"WORK": "green", "PAUSE": "yellow", "TRAVEL": "blue", "SUSPENDED": "magenta", "TO_COMPLETE": "red"}


def _windows_vt():
    """Windows 10+: ANSI sequences once ENABLE_VIRTUAL_TERMINAL_PROCESSING is set."""
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def _supports_colour(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    return _windows_vt() if sys.platform == "win32" else True


class Out:
    def __init__(self, stream=None, colour=None):
        self.stream = stream or sys.stdout
        self.colour = _supports_colour(self.stream) if colour is None else colour

    def paint(self, text, *styles):
        if not self.colour or not styles:
            return text
        return "\033[" + ";".join(_codes[s] for s in styles) + "m" + text + "\033[0m"

    def line(self, text=""):
        print(text, file=self.stream)

    def heading(self, text):
        self.line(self.paint(text.upper(), "bold", "grey"))

    def error(self, text):
        print(self.paint(text, "red"), file=sys.stderr)

    @property
    def width(self):
        return shutil.get_terminal_size((100, 24)).columns


def choose(out, title, options, allow_back=True):
    """
    A numbered list; the index chosen, or None (empty answer, "0", end of
    input). options: [text]; a text may already be painted.
    """
    out.line()
    out.line(out.paint(title, "bold"))
    for i, text in enumerate(options, 1):
        out.line(f"  {out.paint(f'{i:>2}', 'cyan')}  {text}")
    if allow_back:
        out.line(f"  {out.paint(' 0', 'cyan')}  {_('Indietro')}")
    while True:
        try:
            answer = input(out.paint("> ", "bold")).strip()
        except EOFError:
            return None
        if answer in ("", "0", "q"):
            return None
        # ASCII digits only: "²" is a digit to isdigit() but not to int()
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        out.error(_("Scegli un numero da 1 a %d.") % len(options))


def ask(out, label, default=""):
    """A free answer; the default when empty, nothing with "-" (when there is one). None at the end of input."""
    hint = f" [{default}] " + _("(«-» per svuotare)") if default else ""
    try:
        answer = input(f"{label}{hint}: ").strip()
    except EOFError:
        return None
    if default and answer == "-":
        return ""
    return answer or default


def confirm(out, question, default=False):
    yes, no = _("s"), _("n")
    hint = f"{yes.upper()}/{no}" if default else f"{yes}/{no.upper()}"
    try:
        answer = input(f"{question} [{hint}] ").strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer[0] in (yes, "y")
