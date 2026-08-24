#!/usr/bin/env python3
"""Start here.

    uv run run.py                 the interactive runner: pick scope and options
    uv run run.py doctor          can this toolchain actually produce a tagged PDF?
    uv run run.py build sp26/hw/9 --write

`uv run` reads pyproject.toml, creates .venv if it is missing, installs
everything the tool can use, and then runs this. There is no environment to
activate and no `pip install -e .` step to forget.

With no arguments it opens the runner, because that is what someone opening
this file wants. Anything else is passed straight to the `latexally` command,
so this file and the installed entry point cannot drift apart.
"""

import sys

from latexally.cli import main

if __name__ == "__main__":
    main(sys.argv[1:] or ["run"])
