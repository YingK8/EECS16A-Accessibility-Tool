"""The interactive runner.

Built on Textual, and the previous docstring here argued the opposite -- that a
Rich front-end was better because it needed no extra install and was testable
without a pilot harness. Both claims were true and neither survived contact with
a real scope. Rich's ``Live`` clips at the terminal height with no way to
scroll, so sixteen assignments hid the footer that explained how to tick a box,
and every row past the fold was unreachable. A menu layer you cannot scroll is
not a menu layer.

Textual is a hard dependency now, not an extra. The non-interactive route is
``latexally build --config run.yaml``, which is the same engine driven from a
file, so nothing here is on the only path to a conversion.

Two modules:

* :mod:`latexally.tui.summary` -- pure ``(profile, config) -> text``. No widgets.
* :mod:`latexally.tui.app` -- the screens, in the order they are asked.

Four rules the screens follow:

1. **Only scope is asked.** Every other setting has a defensible default, so it
   is *reviewed* rather than *prompted*. Scope is the one thing the tool cannot
   guess, so it is step one rather than a menu entry someone has to be told about.
2. **Every toggle shows its cost.** ``question_tags`` reflows one question in
   five, and a person turning it on is entitled to know that before the run
   rather than after, from the diff.
3. **A step with one possible answer is not a question.** A scope holding only
   homeworks does not ask which kind; a kind holding one assignment does not ask
   which. It says what it decided and moves on.
4. **Nothing is written until you say so.** The review step shows the exact
   preamble and every file touched, and the button after it is the first thing
   that writes.
"""

from __future__ import annotations

from .app import LatexAllyApp
from .summary import show_path

__all__ = ["LatexAllyApp", "show_path"]
