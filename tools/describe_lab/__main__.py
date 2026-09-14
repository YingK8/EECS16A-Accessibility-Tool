"""Author alt text for outstanding figures, in batches.

    python -m tools.describe_lab --scope live --limit 20 --dry-run
    python -m tools.describe_lab --scope live --limit 20

Resume is re-running: a described figure stops being outstanding, so
`next_tasks` never hands it back. There is no state file to corrupt, and an
interrupted run has still done the most-cited figures first.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from latexally.agent import Task, next_tasks, submit  # noqa: E402
from latexally.config import load_profile  # noqa: E402
from latexally.errors import LatexAllyError  # noqa: E402

#: One turn to correct itself, as `docs/AGENT_HARNESS.md` describes. A second
#: retry never once produced a different answer in testing -- the rejections
#: are structural (markup, a banned opener), so an agent that ignores them the
#: first time ignores them again, and the figure is better left for a human.
_ATTEMPTS = 2

_PROMPT = """\
You are writing alt text for one figure in a UC Berkeley EECS course, to be \
read aloud by a screen reader. Reply with the description and nothing else: \
no preamble, no quotes, no markdown.

Rules:
{rules}

{context}
"""


def _context(task: Task) -> str:
    """Everything known about the figure, as plain lines.

    `machine_facts` are deterministic extractions, not a draft. The prompt says
    so, because pasting them back verbatim produces a description of the
    drawing rather than of what it means -- which is rule 4, inverted.
    """
    lines = [f"Figure kind: {task.kind} ({task.genre})"]
    if task.question:
        lines.append(f"Question it belongs to: {task.question}")
    if task.caption:
        lines.append(f"Caption (announced separately -- do NOT repeat it): {task.caption}")
    lines.append(
        "This figure appears in the problem, so do not give away the answer."
        if not task.inside_solution
        else "This figure appears only in the solution."
    )
    if task.image_absolute:
        lines.append(f"Read this image file before answering: {task.image_absolute}")
    elif task.missing_image:
        lines.append(f"The image file {task.image_path!r} is missing; say so rather than guessing.")
    if task.machine_facts:
        lines.append(
            "Facts extracted from the LaTeX source (evidence, not a draft -- do not "
            "paste them back):"
        )
        lines.extend(f"  - {fact}" for fact in task.machine_facts)
    if task.still_needed:
        lines.extend(f"Also: {need}" for need in task.still_needed)
    return "\n".join(lines)


def _ask(command: list[str], prompt: str, timeout: int) -> str:
    """Run the describer and return its answer, squeezed to one line."""
    result = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise LatexAllyError(
            f"describer exited {result.returncode}: {result.stderr.strip()[:200]}",
            hint="check --describer; it must read a prompt on stdin and answer on stdout",
        )
    return " ".join(result.stdout.split())


def _describe_one(task: Task, command: list[str], timeout: int) -> tuple[str, list[dict]]:
    """One description and the rejections it collected on the way, if any."""
    prompt = _PROMPT.format(
        rules="\n".join(f"- {rule}" for rule in task.as_dict()["rules"]),
        context=_context(task),
    )
    rejections: list[dict] = []
    text = ""
    for attempt in range(_ATTEMPTS):
        text = _ask(command, prompt, timeout)
        # Validation runs inside `submit`, before the write, so this asks it the
        # same question first rather than reimplementing the spec here.
        from latexally.agent import validate_description

        problems = [problem.as_dict() for problem in validate_description(text, caption=task.caption)]
        if not problems:
            return text, rejections
        rejections.extend(problems)
        if attempt + 1 < _ATTEMPTS:
            prompt = (
                f"{prompt}\n\nYour previous answer was rejected:\n{text}\n\n"
                + "\n".join(f"- {problem['rule']}: {problem['message']}" for problem in problems)
                + "\n\nWrite it again, correctly. Reply with the description only."
            )
    return text, rejections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--profile", default="ee66", help="course profile")
    parser.add_argument("--scope", default=None, help="narrow to one scope or assignment")
    parser.add_argument("--genre", default=None, help="only figures of this genre")
    parser.add_argument("--limit", type=int, default=10, help="how many figures to describe")
    parser.add_argument("--batch", type=int, default=5, help="figures fetched per round")
    parser.add_argument(
        "--describer",
        default="claude -p",
        help="command that reads a prompt on stdin and writes the description on stdout",
    )
    parser.add_argument("--timeout", type=int, default=300, help="seconds per figure")
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("describe-log.jsonl"),
        help="where every submission is recorded for review",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the descriptions; write nothing",
    )
    args = parser.parse_args()

    profile = load_profile(args.profile)
    command = shlex.split(args.describer)
    written = skipped = 0

    with args.log.open("a", encoding="utf-8") as log:
        while written + skipped < args.limit:
            remaining = args.limit - written - skipped
            tasks = next_tasks(
                profile,
                limit=min(args.batch, remaining),
                genre=args.genre,
                scope=args.scope,
            )
            if not tasks:
                break
            # A dry run never writes, so the same tasks come back every round
            # and the loop would not end. One round is all it can honestly do.
            for task in tasks:
                try:
                    text, rejections = _describe_one(task, command, args.timeout)
                except (LatexAllyError, subprocess.TimeoutExpired) as exc:
                    print(f"  !! {task.id}: {exc}", file=sys.stderr)
                    skipped += 1
                    continue

                record = {
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "id": task.id,
                    "genre": task.genre,
                    "call_sites": task.call_sites,
                    "files": task.files[:3],
                    "caption": task.caption,
                    "description": text,
                    "rejections": rejections,
                    "written": False,
                }
                if args.dry_run:
                    print(f"  {task.id} ({task.genre}, {task.call_sites} sites)\n    {text}")
                else:
                    result = submit(profile, task.id, description=text)
                    record["written"] = bool(result.get("accepted"))
                    if not result["accepted"]:
                        record["rejections"] = result["rejections"]
                        print(f"  !! {task.id} rejected: {result['rejections']}", file=sys.stderr)
                        skipped += 1
                    else:
                        written += 1
                        print(f"  ok {task.id}: {text}")
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()
            if args.dry_run:
                break

    if args.dry_run:
        print(f"\ndry run: nothing written. Log: {args.log}")
        return 0
    print(f"\n{written} written, {skipped} skipped. Read {args.log} before shipping.")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
