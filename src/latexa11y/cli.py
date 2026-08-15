"""Command line interface.

Two hard rules, because this CLI has two very different consumers:

1. **Every command supports ``--json``.** LLM agents and CI drive the same code
   paths humans do; nothing is TUI-only. A command that can only print a table
   is a command an agent cannot use.
2. **Every failure names the next action.** Errors carry a `hint`, and the exit
   code distinguishes "found problems" (1) from "could not run" (2), so a script
   can tell a genuine finding from a broken environment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .config import Profile, load_profile
from .errors import LatexA11yError
from .toolchain import Status, TaggingMode, probe

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

_STATUS_STYLE = {
    Status.OK: ("[green]ok[/green]", "green"),
    Status.WARN: ("[yellow]warn[/yellow]", "yellow"),
    Status.FAIL: ("[red]FAIL[/red]", "red"),
    Status.SKIP: ("[dim]skip[/dim]", "dim"),
}


class Context:
    """Shared state resolved once and handed to every subcommand."""

    def __init__(self, profile: Profile, *, as_json: bool, quiet: bool) -> None:
        self.profile = profile
        self.as_json = as_json
        self.console = Console(stderr=as_json, quiet=quiet and not as_json)

    def emit(self, payload: dict) -> None:
        if self.as_json:
            click.echo(json.dumps(payload, indent=2, default=str))


pass_context = click.make_pass_decorator(Context)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="latexa11y")
@click.option(
    "--profile",
    "-p",
    default=None,
    help="Course profile: a builtin name (e.g. eecs16a) or a path to a YAML file.",
)
@click.option(
    "--corpus",
    "-c",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Corpus root, overriding the profile's corpus.root.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress human-readable output.")
@click.pass_context
def main(
    ctx: click.Context,
    profile: str | None,
    corpus: Path | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Convert LaTeX instructional materials into tagged, PDF/UA-conforming PDFs."""
    try:
        loaded = load_profile(profile, corpus_root=corpus)
    except LatexA11yError as exc:
        click.echo(f"error: {exc}", err=True)
        ctx.exit(EXIT_ERROR)
    ctx.obj = Context(loaded, as_json=as_json, quiet=quiet)


# ---------------------------------------------------------------------- #
# doctor
# ---------------------------------------------------------------------- #


@main.command()
@click.option(
    "--strict",
    is_flag=True,
    help="Treat warnings as failures. Use in CI before claiming conformance.",
)
@pass_context
def doctor(ctx: Context, strict: bool) -> None:
    """Check that the toolchain can actually produce a conforming PDF.

    Run this before anything else. A LaTeX accessibility toolchain fails
    silently by default: a missing testphase module or an unsupported
    `pdfstandard` value produces an untagged PDF with no error, which is far
    worse than a build failure when the output carries a legal obligation.
    """
    report = probe(ctx.profile)

    if ctx.as_json:
        ctx.emit(report.as_dict())
    else:
        console = ctx.console
        table = Table(
            title=f"latexa11y doctor — profile: {ctx.profile.name}",
            title_justify="left",
            header_style="bold",
        )
        table.add_column("", width=4)
        table.add_column("Check", style="bold", no_wrap=True)
        table.add_column("Detail", overflow="fold")
        for check in report.checks:
            marker, style = _STATUS_STYLE[check.status]
            # Details carry LaTeX and bracketed lists; escape them or Rich
            # silently eats `[pdf,tex,tui]` as a style tag.
            table.add_row(marker, check.label, f"[{style}]{escape(check.detail)}[/{style}]")
        console.print(table)

        for check in report.checks:
            if check.hint and check.status in (Status.FAIL, Status.WARN):
                console.print(
                    f"  [dim]{check.id}[/dim] {escape(check.label)}: {escape(check.hint)}"
                )

        console.print()
        mode = report.tagging_mode
        if mode is TaggingMode.MODERN:
            console.print("[green]Tagging mode: modern[/green] (`tagging=on`, PDF/UA declarable)")
        elif mode is TaggingMode.LEGACY_TESTPHASE:
            console.print(
                "[yellow]Tagging mode: legacy testphase[/yellow] — documents will be "
                "tagged, but this toolchain cannot declare PDF/UA conformance in the "
                "PDF metadata. Do not claim compliance from this configuration."
            )
        else:
            console.print(
                "[red]Tagging mode: unavailable[/red] — a build here would silently "
                "produce an untagged PDF. The pipeline will refuse to run."
            )

    blocking = bool(report.failures) or report.tagging_mode is TaggingMode.UNAVAILABLE
    if strict and report.warnings:
        blocking = True
    sys.exit(EXIT_FINDINGS if blocking else EXIT_OK)


# ---------------------------------------------------------------------- #
# corpus inspection
# ---------------------------------------------------------------------- #


@main.command()
@click.argument("scope", required=False)
@click.option("--documents", is_flag=True, help="List only compilable documents.")
@click.option("--limit", type=int, default=0, help="Show at most N paths (0 = all).")
@pass_context
def files(ctx: Context, scope: str | None, documents: bool, limit: int) -> None:
    """List the source files a scope resolves to.

    Useful for confirming that excludes actually exclude what you think: in the
    EECS 16A corpus, 17k of 17.6k .tex files are frozen per-semester snapshots.
    """
    from .texlex.includes import IncludeGraph

    try:
        paths = list(ctx.profile.iter_files(scope))
    except LatexA11yError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(EXIT_ERROR)

    root = ctx.profile.corpus.root.resolve()
    if documents:
        graph = IncludeGraph([root]).build(paths)
        selected = sorted(graph.documents)
    else:
        selected = paths

    shown = selected[:limit] if limit else selected
    if ctx.as_json:
        ctx.emit(
            {
                "scope": scope,
                "root": str(root),
                "count": len(selected),
                "documents_only": documents,
                "files": [str(path.relative_to(root)) for path in shown],
            }
        )
    else:
        for path in shown:
            ctx.console.print(str(path.relative_to(root)))
        suffix = " documents" if documents else " files"
        if limit and len(selected) > limit:
            ctx.console.print(f"[dim]... {len(selected) - limit} more[/dim]")
        ctx.console.print(f"[bold]{len(selected)}[/bold]{suffix} in scope {scope or 'default'}")
    sys.exit(EXIT_OK)


# ---------------------------------------------------------------------- #
# scan
# ---------------------------------------------------------------------- #


@main.command()
@click.argument("scope", required=False)
@click.option(
    "--no-write", is_flag=True, help="Report only; do not create or update worklogs."
)
@pass_context
def scan(ctx: Context, scope: str | None, no_write: bool) -> None:
    """Find every figure, derive a description skeleton, refresh the worklogs.

    Safe to re-run: machine sections are regenerated, and human-written alt
    text, status and disposition are always preserved.
    """
    from .catalog import build_catalog, worklog_dir

    result = build_catalog(ctx.profile, scope, write=not no_write)
    payload = result.as_dict() | {"scope": scope, "directory": str(worklog_dir(ctx.profile))}

    if ctx.as_json:
        ctx.emit(payload)
    else:
        console = ctx.console
        console.print(
            f"[bold]{result.call_sites}[/bold] call sites → "
            f"[bold]{result.unique}[/bold] unique figures "
            f"({result.call_sites / max(1, result.unique):.2f}× deduplication)"
        )
        console.print(f"described: [green]{result.done}[/green]   "
                      f"outstanding: [yellow]{len(result.outstanding)}[/yellow]")
        if not no_write:
            console.print(f"worklogs: {worklog_dir(ctx.profile)} ({len(result.worklogs)} files)")
        by_genre: dict[str, int] = {}
        for entry in result.outstanding:
            by_genre[entry.genre] = by_genre.get(entry.genre, 0) + 1
        for genre, count in sorted(by_genre.items(), key=lambda item: -item[1]):
            console.print(f"  [dim]{genre:<16}[/dim] {count} to describe")
    sys.exit(EXIT_FINDINGS if result.outstanding else EXIT_OK)


# ---------------------------------------------------------------------- #
# apply
# ---------------------------------------------------------------------- #


@main.command()
@click.argument("scope", required=False)
@click.option("--write", is_flag=True, help="Actually modify files (default is a dry run).")
@click.option("--show-diff", is_flag=True, help="Print the unified diff for each file.")
@pass_context
def apply(ctx: Context, scope: str | None, write: bool, show_diff: bool) -> None:
    """Write approved descriptions into the .tex sources.

    Defaults to a dry run. Only descriptions marked `approved` are written; a
    draft or empty description is skipped rather than shipped.
    """
    from .apply.figures import apply_scope
    from .catalog import load_entries

    entries = load_entries(ctx.profile)
    if not entries:
        click.echo("error: no worklogs found; run `latexa11y scan` first", err=True)
        sys.exit(EXIT_ERROR)

    plans = apply_scope(ctx.profile, scope, entries, dry_run=not write)
    wrapped = sum(plan.wrapped for plan in plans)
    artifacts = sum(plan.artifacts for plan in plans)
    skipped = [item for plan in plans for item in plan.skipped]

    if ctx.as_json:
        ctx.emit(
            {
                "dry_run": not write,
                "files_changed": sum(1 for plan in plans if plan.changed),
                "figures_wrapped": wrapped,
                "artifacts_marked": artifacts,
                "skipped": [{"id": fid, "reason": reason} for fid, reason in skipped],
                "diffs": {str(plan.path): plan.diff() for plan in plans if show_diff and plan.changed},
            }
        )
    else:
        console = ctx.console
        mode = "would change" if not write else "changed"
        console.print(
            f"{mode} [bold]{sum(1 for plan in plans if plan.changed)}[/bold] files: "
            f"{wrapped} figures described, {artifacts} marked decorative"
        )
        if skipped:
            console.print(f"[yellow]skipped {len(skipped)}[/yellow] (not yet approved):")
            for fid, reason in skipped[:8]:
                console.print(f"  [dim]{fid}[/dim] {escape(reason)}")
        if show_diff:
            for plan in plans:
                if plan.changed:
                    console.print(escape(plan.diff()))
        if not write and any(plan.changed for plan in plans):
            console.print("\n[dim]re-run with --write to apply[/dim]")
    sys.exit(EXIT_OK)


# ---------------------------------------------------------------------- #
# check
# ---------------------------------------------------------------------- #


@main.command()
@click.argument("scope", required=False)
@click.option("--pdf", type=click.Path(path_type=Path), help="Also check a built PDF.")
@click.option("--log", "logfile", type=click.Path(path_type=Path), help="Also check a build log.")
@click.option(
    "--severity",
    type=click.Choice(["error", "warning", "info"]),
    default="warning",
    help="Minimum severity to report.",
)
@pass_context
def check(
    ctx: Context, scope: str | None, pdf: Path | None, logfile: Path | None, severity: str
) -> None:
    """Validate conformance: source lint, build log, and PDF structure."""
    from .check.rules import Severity, check_log, check_pdf_structure, check_source

    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings = []
    for path in ctx.profile.iter_files(scope):
        if path.suffix.lower() not in (".tex", ".sty", ".cls"):
            continue
        try:
            findings.extend(check_source(path, ctx.profile))
        except Exception:
            continue
    if logfile:
        findings.extend(check_log(Path(logfile)))
    if pdf:
        findings.extend(check_pdf_structure(Path(pdf)))

    threshold = order[severity]
    findings = [item for item in findings if order[item.severity] <= threshold]
    findings.sort(key=lambda item: (order[item.severity], item.rule, item.file or "", item.line or 0))
    errors = [item for item in findings if item.severity == Severity.ERROR]

    if ctx.as_json:
        ctx.emit(
            {
                "scope": scope,
                "total": len(findings),
                "errors": len(errors),
                "findings": [item.as_dict() for item in findings],
            }
        )
    else:
        console = ctx.console
        root = ctx.profile.corpus.root.resolve()
        counts: dict[str, int] = {}
        for item in findings:
            counts[item.rule] = counts.get(item.rule, 0) + 1
        for item in findings[:60]:
            where = item.file or ""
            try:
                where = str(Path(where).resolve().relative_to(root))
            except (ValueError, OSError):
                pass
            location = f"{where}:{item.line}" if item.line else where
            colour = {"error": "red", "warning": "yellow", "info": "dim"}[item.severity]
            console.print(
                f"[{colour}]{item.severity:<7}[/{colour}] {item.rule}  "
                f"{escape(location)}\n          {escape(item.message)}"
            )
        if len(findings) > 60:
            console.print(f"[dim]…{len(findings) - 60} more[/dim]")
        console.print(
            f"\n[bold]{len(findings)}[/bold] findings "
            f"([red]{len(errors)} errors[/red]) across {len(counts)} rules"
        )
    sys.exit(EXIT_FINDINGS if errors else EXIT_OK)


# ---------------------------------------------------------------------- #
# build / run — the conversion pipeline
# ---------------------------------------------------------------------- #


def _load_run_config(
    ctx: Context,
    config_path: Path | None,
    assignments: tuple[str, ...],
    output: Path | None,
    write: bool,
) -> "RunConfig":  # noqa: F821
    from .run import Output, RunConfig

    config = RunConfig.load(config_path) if config_path else RunConfig(profile=ctx.profile.name)
    if assignments:
        config = config.with_assignments(assignments)
    if output is not None:
        config.output = Output(
            root=output,
            write_mode=config.output.write_mode,
            keep_pdf=config.output.keep_pdf,
            keep_logs=config.output.keep_logs,
            keep_tex=config.output.keep_tex,
        )
    config.write = write
    return config


def _report_table(reports: list) -> Table:
    table = Table(header_style="bold", title_justify="left")
    table.add_column("assignment", no_wrap=True)
    table.add_column("", width=2)
    table.add_column("pages", justify="right")
    table.add_column("bookmarks", justify="right")
    table.add_column("figures", justify="right")
    table.add_column("errors", justify="right")
    table.add_column("warnings", justify="right")
    table.add_column("pixel diff", justify="right")
    for report in reports:
        diff = (
            f"{100 * report.pixel_diff:.2f}%"
            if report.pixel_diff is not None
            else f"[dim]{escape(report.diff_note or '—')}[/dim]"
        )
        table.add_row(
            report.assignment,
            "[green]✓[/green]" if report.ok else "[red]✗[/red]",
            str(report.pages if report.pages is not None else "—"),
            str(report.bookmarks if report.bookmarks is not None else "—"),
            str(report.figures if report.figures is not None else "—"),
            f"[red]{len(report.errors)}[/red]" if report.errors else "0",
            f"[yellow]{len(report.tagpdf_warnings)}[/yellow]"
            if report.tagpdf_warnings
            else "0",
            diff,
        )
    return table


@main.command()
@click.argument("assignments", nargs=-1)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    help="Replay a run.yaml written by `latexa11y run`.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where PDFs, logs, converted sources and worklogs go.",
)
@click.option("--write", is_flag=True, help="Actually build (default is a dry run).")
@click.option(
    "--in-place",
    is_flag=True,
    help="Edit the corpus sources directly. Refuses on a dirty git worktree.",
)
@click.option("--question-tags", is_flag=True, help="Emit real H2 tags for question titles.")
@click.option("--house-colors", is_flag=True, help="Keep the course palette, contrast and all.")
@click.option(
    "--placeholders",
    is_flag=True,
    help="Mark undescribed figures in the source. Strict mode makes them build-failing.",
)
@pass_context
def build(
    ctx: Context,
    assignments: tuple[str, ...],
    config_path: Path | None,
    output: Path | None,
    write: bool,
    in_place: bool,
    question_tags: bool,
    house_colors: bool,
    placeholders: bool,
) -> None:
    """Convert and build assignments. This is what `latexa11y run` runs.

    Every flag here corresponds to a screen in the interactive runner, and both
    paths end up calling the same engine — so a run can be explored in the TUI,
    saved, and replayed unchanged in CI.
    """
    from .build import build_run, describe_run
    from .run import AltChoice, ColorChoice, Output

    config = _load_run_config(ctx, config_path, assignments, output, write)
    if in_place:
        config.output = Output(
            root=config.output.root,
            write_mode="in-place",
            keep_pdf=config.output.keep_pdf,
            keep_logs=config.output.keep_logs,
            keep_tex=config.output.keep_tex,
        )
    if question_tags:
        config.standards.question_tags = True
    if house_colors:
        config.colors = ColorChoice(mode="house")
    if placeholders:
        config.alt = AltChoice(mode="placeholders", strict=config.alt.strict)

    if not config.assignments:
        click.echo(
            "error: nothing to build; name assignments or pass --config", err=True
        )
        sys.exit(EXIT_ERROR)

    try:
        descriptions = describe_run(config, ctx.profile)
        reports = build_run(config, ctx.profile)
    except LatexA11yError as exc:
        if ctx.as_json:
            ctx.emit({"ok": False, "error": str(exc)})
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(EXIT_ERROR)

    failures = [report for report in reports if not report.ok]
    if ctx.as_json:
        ctx.emit(
            {
                "dry_run": not config.write,
                "config": config.as_dict(),
                "descriptions": descriptions,
                "reports": [report.as_dict() for report in reports],
                "failed": len(failures),
            }
        )
    else:
        console = ctx.console
        if not config.write:
            console.print("[bold yellow]DRY RUN[/bold yellow] — nothing written\n")
            console.print("Would inject into each driver:")
            for line in reports[0].injected if reports else []:
                console.print(f"  [cyan]{escape(line)}[/cyan]")
            console.print()
            for report in reports:
                console.print(f"  {report.assignment}  ({report.driver})")
            console.print("\n[dim]re-run with --write to build[/dim]")
        else:
            console.print(_report_table(reports))
            if descriptions.get("scanned"):
                console.print(
                    f"\ndescriptions: [bold]{descriptions['unique']}[/bold] figures "
                    f"across {descriptions['call_sites']} call sites in "
                    f"{descriptions['files']} files — "
                    f"[green]{descriptions['described']} done[/green], "
                    f"[yellow]{descriptions['outstanding']} outstanding[/yellow]"
                )
                for path in descriptions.get("worklogs", [])[:5]:
                    console.print(f"  [dim]{escape(path)}[/dim]")
            _print_failures(console, failures)
    sys.exit(EXIT_FINDINGS if failures else EXIT_OK)


def _print_failures(console: Console, failures: list) -> None:
    """Say what went wrong, and where to look next.

    Shared by `build` and `run`: the interactive path used to show a ✗ in the
    table and nothing else, which tells a user that something failed and gives
    them no way to find out what.
    """
    for report in failures:
        console.print(f"\n[red]{escape(report.assignment)} failed[/red]")
        if report.note:
            console.print(f"  {escape(report.note)}")
        for line in report.errors[:5]:
            console.print(f"  [red]{escape(line)}[/red]")
        if len(report.errors) > 5:
            console.print(f"  [dim]…{len(report.errors) - 5} more[/dim]")
        if report.log:
            from .tui import show_path

            console.print(f"  [dim]full log: {escape(show_path(report.log))}[/dim]")
    if failures:
        # Most build failures in this corpus are constructs LaTeX's own tagging
        # cannot handle, and `check` names the file and line in milliseconds
        # rather than after another three-minute compile.
        console.print(
            "\n[dim]`latexa11y check <scope>` locates constructs that tagging "
            "cannot compile, without rebuilding.[/dim]"
        )


@main.command("run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    help="Start from a saved run.yaml instead of the defaults.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    help="Preset the output directory.",
)
@pass_context
def run_command(ctx: Context, config_path: Path | None, output: Path | None) -> None:
    """Interactive runner: choose scope, standards, colours and output, then build."""
    from .build import build_run, describe_run
    from .tui import Wizard

    config = _load_run_config(ctx, config_path, (), output, False)
    wizard = Wizard(ctx.profile, config, console=Console())
    config = wizard.loop()

    if not wizard.should_run:
        ctx.console.print("[dim]nothing built[/dim]")
        sys.exit(EXIT_OK)

    wizard.save()
    descriptions = describe_run(config, ctx.profile)
    reports = build_run(
        config,
        ctx.profile,
        on_start=lambda item: wizard.console.print(f"[dim]building {item.path}…[/dim]"),
    )
    wizard.console.print(_report_table(reports))
    if descriptions.get("scanned") and descriptions.get("outstanding"):
        wizard.console.print(
            f"\n[bold]{descriptions['outstanding']}[/bold] figure(s) still need "
            f"alt text. Fill them in:"
        )
        for path in descriptions.get("worklogs", [])[:8]:
            wizard.console.print(f"  {escape(path)}")

    failures = [report for report in reports if not report.ok]
    _print_failures(wizard.console, failures)
    sys.exit(EXIT_FINDINGS if failures else EXIT_OK)


# ---------------------------------------------------------------------- #
# agent harness
# ---------------------------------------------------------------------- #


@main.group()
def agent() -> None:
    """Machine-readable interface for LLM agents and scripts."""


@agent.command("next-task")
@click.option("--limit", type=int, default=1, help="How many tasks to return.")
@click.option("--genre", default=None, help="Restrict to one genre (circuit, plot, image…).")
@click.option("--refresh", is_flag=True, help="Re-scan the corpus before selecting.")
@pass_context
def agent_next_task(ctx: Context, limit: int, genre: str | None, refresh: bool) -> None:
    """Return self-contained description tasks, highest-value first."""
    from .agent.tasks import next_tasks

    tasks = next_tasks(ctx.profile, limit=limit, genre=genre, refresh=refresh)
    payload = {"count": len(tasks), "tasks": [task.as_dict() for task in tasks]}
    if ctx.as_json:
        ctx.emit(payload)
    else:
        if not tasks:
            ctx.console.print("[green]nothing outstanding[/green]")
        for task in tasks:
            ctx.console.print(f"[bold]{task.id}[/bold]  {task.genre}  ×{task.call_sites} sites")
            if task.question:
                ctx.console.print(f"  question: {escape(task.question[:100])}")
            for fact in task.machine_facts[:4]:
                ctx.console.print(f"  [dim]fact:[/dim] {escape(fact[:100])}")
            for need in task.still_needed[:2]:
                ctx.console.print(f"  [yellow]needs:[/yellow] {escape(need[:100])}")
    sys.exit(EXIT_OK)


@agent.command("submit")
@click.option("--id", "identity", required=True, help="Figure id from next-task.")
@click.option("--description", required=True, help="The short description.")
@click.option("--long", "long_text", default="", help="Optional long description.")
@click.option("--notes", default="", help="Optional reviewer notes.")
@click.option("--author", default="agent", help="Who wrote it.")
@click.option(
    "--disposition",
    type=click.Choice(["figure", "artifact"]),
    default=None,
    help="Mark the graphic decorative instead of describing it.",
)
@pass_context
def agent_submit(
    ctx: Context,
    identity: str,
    description: str,
    long_text: str,
    notes: str,
    author: str,
    disposition: str | None,
) -> None:
    """Propose a description. Always recorded as needs-review, never approved."""
    from .agent.tasks import submit

    try:
        result = submit(
            ctx.profile,
            identity,
            description=description,
            long_description=long_text,
            notes=notes,
            author=author,
            disposition=disposition,
        )
    except LatexA11yError as exc:
        if ctx.as_json:
            ctx.emit({"accepted": False, "error": str(exc)})
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(EXIT_ERROR)

    if ctx.as_json:
        ctx.emit(result)
    elif result["accepted"]:
        ctx.console.print(f"[green]recorded[/green] {identity} → {result['status']}")
    else:
        ctx.console.print("[red]rejected:[/red]")
        for problem in result["rejections"]:
            ctx.console.print(f"  {problem['rule']}: {escape(problem['message'])}")
    sys.exit(EXIT_OK if result.get("accepted") else EXIT_FINDINGS)


@agent.command("rules")
@pass_context
def agent_rules(ctx: Context) -> None:
    """Print the alt-text authoring spec an agent must follow."""
    from .agent.tasks import AUTHORING_RULES

    if ctx.as_json:
        ctx.emit({"rules": list(AUTHORING_RULES)})
    else:
        for rule in AUTHORING_RULES:
            ctx.console.print(f"• {escape(rule)}")
    sys.exit(EXIT_OK)


if __name__ == "__main__":  # pragma: no cover
    main()
