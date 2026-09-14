"""The description-authoring harness. Not part of the package.

536 figures in the live scope need a sentence each, and `src/latexally/`
cannot write one: it is the deterministic pipeline, and
``tests/test_no_ai_in_production.py`` keeps a model out of it. The describers
under ``src/latexally/describe/`` therefore stop at *facts* -- circuit
topology, plot axes, node labels -- and never guess at meaning.

This is where the guessing is allowed to happen, one level up, outside the
wheel.

The wall, and how this respects it
----------------------------------
The test also forbids a model-provider package in ``[project.dependencies]``,
and it reads every dependency group, so ``pip install anthropic`` is not
available even as an extra. So this shells out to a command instead of
importing an SDK: ``claude -p`` by default, anything that reads a prompt on
stdin and writes an answer on stdout via ``--describer``. Zero new
dependencies, and the wall holds by construction rather than by promise.

What it does NOT do
-------------------
It does not review. ``docs/AGENT_HARNESS.md`` is explicit that whatever
``agent submit`` writes is what reaches a student, and this submits in a loop,
so it can write 536 unread sentences into course material under a legal
obligation faster than anyone can read them. Every submission is appended to a
JSONL log for exactly that reason. **Read the log.**

The loop
--------
1. ``latexally -p ee66 scan live``            worklogs must exist first; submit
                                              writes into them and will not
                                              invent a file
2. ``python -m tools.describe_lab --scope live --limit 20 --dry-run``
                                              see the sentences without writing
3. ``python -m tools.describe_lab --scope live --limit 20``
                                              write them
4. read ``describe-log.jsonl``
5. ``latexally -p ee66 figures``              the burn-down
"""
