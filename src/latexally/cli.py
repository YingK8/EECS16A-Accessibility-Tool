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
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .config import Profile, load_profile
from .discover import scope_from_cwd
from .errors import LatexAllyError
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

    def __init__(
        self,
        profile: Profile,
        *,
        as_json: bool,
        quiet: bool,
        here_scope: str | None = None,
    ) -> None:
        self.profile = profile
        self.as_json = as_json
        # emoji=False, because this tool prints file:line references all day
        # and Rich reads `:100:` as an emoji shortcode. A real error line came
        # out as `q_image_compression.tex💯 Package latexally Error`, which is
        # not a location anyone can open. `:8ball:`, `:x:` and `:v:` are the
        # same hazard on other line numbers and in LaTeX source.
        self.console = Console(
            stderr=as_json, quiet=quiet and not as_json, emoji=False
        )
        #: Corpus-relative path of the directory `--here` was run from, or None.
        #: Commands that take a scope fall back to it when given none.
        self.here_scope = here_scope

    def scope_or_here(self, scope: str | None) -> str | None:
        """An explicit scope wins; otherwise `--here` supplies one.

        Every scope-taking command routes through this, so `--here` cannot be
        a flag that works on two of them and silently does nothing on the rest.
        """
        return scope if scope is not None else self.here_scope

    def emit(self, payload: dict) -> None:
        if self.as_json:
            click.echo(json.dumps(payload, indent=2, default=str))


pass_context = click.make_pass_decorator(Context)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--profile",
    "-p",
    default=None,
    help=(
        "Course profile: a builtin name (e.g. eecs16a) or a path to a YAML file. "
        "Optional while only one profile is installed, which is then used."
    ),
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
    """Convert LaTeX instructional materials into tagged, PDF/UA-conforming PDFs.

    Where you run it is the scope. Standing in `sp26/hw/10` works on that
    assignment; standing at the top of the corpus works on all of it. Nothing
    to pass, and no flag to remember.
    """
    try:
        loaded = load_profile(profile, corpus_root=corpus)
    except LatexAllyError as exc:
        click.echo(f"error: {exc}", err=True)
        ctx.exit(EXIT_ERROR)
    ctx.obj = Context(loaded, as_json=as_json, quiet=quiet, here_scope=scope_from_cwd(loaded))


# ---------------------------------------------------------------------- #
# doctor
# ---------------------------------------------------------------------- #


@main.command()
@click.argument("scope", required=False)
@click.option(
    "--strict",
    is_flag=True,
    help="Treat warnings as failures. Use in CI before claiming conformance.",
)
@click.option(
    # Not `--corpus`: that is already a global option naming the corpus ROOT,
    # and one word meaning two things a flag apart is how people mistype.
    "--tagging",
    is_flag=True,
    help="Scan the source for constructs LaTeX's tagging cannot compile.",
)
@click.option(
    "--fix",
    is_flag=True,
    help="With --tagging: rewrite what can be rewritten. Shows a diff unless --write.",
)
@click.option(
    "--write",
    is_flag=True,
    help="With --tagging --fix: actually edit the corpus. Refuses on a dirty git worktree.",
)
@pass_context
def doctor(
    ctx: Context, scope: str | None, strict: bool, tagging: bool, fix: bool, write: bool
) -> None:
    """Check that the toolchain can actually produce a conforming PDF.

    Run this before anything else. A LaTeX accessibility toolchain fails
    silently by default: a missing testphase module or an unsupported
    `pdfstandard` value produces an untagged PDF with no error, which is far
    worse than a build failure when the output carries a legal obligation.
    """
    scope = ctx.scope_or_here(scope)
    if (fix or write) and not tagging:
        click.echo("error: --fix and --write only apply to --tagging", err=True)
        sys.exit(EXIT_ERROR)
    if tagging:
        _doctor_tagging(ctx, scope, fix=fix, write=write)
        return

    report = probe(ctx.profile)

    if ctx.as_json:
        ctx.emit(report.as_dict())
    else:
        console = ctx.console
        table = Table(
            title=f"latexally doctor — profile: {ctx.profile.name}",
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
    scope = ctx.scope_or_here(scope)
    from .texlex.includes import IncludeGraph

    try:
        paths = list(ctx.profile.iter_files(scope))
    except LatexAllyError as exc:
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
    r"""Find every figure, derive a description skeleton, refresh the worklogs.

    Safe to re-run: the figure list is rebuilt from the source every time and
    human-written alt text is never overwritten.

    Scans what each document CONTAINS, not what its folder holds. An assignment
    here is a thin wrapper that ``\input``s its questions from the shared bank:
    ``sp26/dis/13A`` owns five figures in a ``questions/`` folder the document
    never includes, and renders four others from ``questionBank/sec/13``. A
    folder-scoped scan described the five orphans and none of the four, then
    reported a clean sweep. Measured across sp26, 76.5% of graphics are reached
    by ``\input`` rather than living in the assignment's own folder.

    ``build`` has always resolved the real file set this way; only this command
    did not, so a worklog filled in by hand disagreed with the one a build
    produced.
    """
    scope = ctx.scope_or_here(scope)
    from .build import source_files_for
    from .catalog import build_catalog, worklog_dir
    from .discover import discover_assignments

    try:
        files: list[Path] = []
        for assignment in discover_assignments(ctx.profile, scope):
            files.extend(source_files_for(assignment, ctx.profile))

        # No assignment in scope means the scope is a bare glob, not a document.
        # Fall back to it rather than scanning nothing.
        result = (
            build_catalog(
                ctx.profile, files=sorted(set(files)), write=not no_write
            )
            if files
            else build_catalog(ctx.profile, scope, write=not no_write)
        )
    except LatexAllyError as exc:
        # This command had no handler at all, so a CatalogError surfaced as a
        # traceback -- against this module's own rule that every failure names
        # the next action.
        if ctx.as_json:
            ctx.emit({"ok": False, "error": str(exc)})
        else:
            # `str(exc)` already carries the hint; printing it again is noise.
            click.echo(f"error: {exc}", err=True)
        sys.exit(EXIT_ERROR)
    directory = str(worklog_dir(ctx.profile))
    payload = result.as_dict() | {"scope": scope, "directory": directory}

    if ctx.as_json:
        ctx.emit(payload)
    else:
        console = ctx.console
        # "1.00× deduplication" is a ratio nobody asked for, and at 1.00 it
        # says nothing at all. The useful fact is how much work the figures
        # represent, and -- when a figure is reused -- how much of it you get
        # for free by describing it once.
        if result.call_sites > result.unique:
            saved = result.call_sites - result.unique
            console.print(
                f"[bold]{result.unique}[/bold] figures to describe, appearing at "
                f"[bold]{result.call_sites}[/bold] places — describing each once "
                f"covers {saved} further use(s)"
            )
        else:
            console.print(
                f"[bold]{result.unique}[/bold] figures to describe, each used once"
            )
        console.print(f"described: [green]{result.done}[/green]   "
                      f"outstanding: [yellow]{len(result.outstanding)}[/yellow]")
        if not no_write:
            console.print(f"worklogs: {directory} ({len(result.worklogs)} files)")
        by_genre: dict[str, int] = {}
        for entry in result.outstanding:
            by_genre[entry.genre] = by_genre.get(entry.genre, 0) + 1
        for genre, count in sorted(by_genre.items(), key=lambda item: -item[1]):
            console.print(f"  [dim]{genre:<16}[/dim] {count} to describe")
    sys.exit(EXIT_FINDINGS if result.outstanding else EXIT_OK)


# ---------------------------------------------------------------------- #
# apply
# ---------------------------------------------------------------------- #


def _files_to_check(profile: Profile, scope: str | None) -> set[Path]:
    """Every file the scope compiles, not merely every file inside it.

    A scope glob finds `sp26/dis/01A/*.tex`; the drivers in there `\\input` the
    shared question bank, and that is where most of the constructs tagging
    cannot compile actually live. Checking only the directory reported a clean
    assignment whose PDF then came out with every list numbered zero.
    """
    from .build import source_files_for
    from .discover import discover_assignments

    files = set(profile.iter_files(scope))
    if scope is None:
        # No scope is already the whole corpus; following includes would walk
        # the same files again and add nothing but seconds.
        return files
    try:
        assignments = discover_assignments(profile, scope)
    except LatexAllyError:
        return files
    for assignment in assignments:
        if not assignment.buildable:
            continue
        try:
            files.update(source_files_for(assignment, profile))
        except (LatexAllyError, OSError):
            continue
    return files


@main.command()
@click.argument("scope", required=False)
@click.option("--write", is_flag=True, help="Actually modify files (default is a dry run).")
@click.option("--show-diff", is_flag=True, help="Print the unified diff for each file.")
@pass_context
def apply(ctx: Context, scope: str | None, write: bool, show_diff: bool) -> None:
    r"""Write descriptions into the .tex sources.

    Defaults to a dry run. A figure with no description written is skipped
    rather than shipped.

    Resolves the same file set ``scan`` does: what each document \input s, not
    what its folder holds. Scanning one way and applying the other is worse than
    either alone -- ``scan`` describes the figures the document renders and
    ``apply`` then walks a folder full of different ones, reporting every id as
    "not in any worklog; run scan first" immediately after a successful scan.
    """
    scope = ctx.scope_or_here(scope)
    from .apply import apply_scope
    from .build import source_files_for
    from .catalog import load_entries
    from .discover import discover_assignments

    entries = load_entries(ctx.profile)
    if not entries:
        click.echo("error: no worklogs found; run `latexally scan` first", err=True)
        sys.exit(EXIT_ERROR)

    files: list[Path] = []
    for assignment in discover_assignments(ctx.profile, scope):
        files.extend(source_files_for(assignment, ctx.profile))

    plans = apply_scope(
        ctx.profile,
        scope,
        entries,
        dry_run=not write,
        files=sorted(set(files)) or None,
    )
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


def _doctor_tagging(ctx: Context, scope: str | None, *, fix: bool, write: bool) -> None:
    """The source tier of `doctor`: will this corpus build under tagging?

    Separate from `check` on purpose. `check` answers "is this document
    accessible" and every rule it reports cites WCAG or Matterhorn. The
    constructs here cite neither: they are LaTeX that pdfLaTeX has always
    accepted and that `\\DocumentMetadata{testphase={tagpdf}}` rejects. Mixing
    them into the accessibility report made a build blocker look like a
    conformance failure and buried both.

    Opt-in behind `--tagging` because it reads every file in scope, and the
    environment tier above it is meant to be cheap enough to run before every
    command.
    """
    from .build import require_clean_worktree
    from .check.rules import check_tagging
    from .rewrite import FIXED_BY_TAGGING, RULES, plan_rewrites
    from .toolchain import TaggingMode, probe

    # Which rules still need rewriting depends on the toolchain: `tagging=on`
    # handles some of them itself, and rewriting those would be churn.
    mode = probe(ctx.profile).tagging_mode
    skip = FIXED_BY_TAGGING if mode is TaggingMode.MODERN else frozenset()

    if write:
        # The same guard `build --in-place` uses, and for the same reason: the
        # only thing that makes 588 rewritten files revertible is git.
        require_clean_worktree(Path(ctx.profile.corpus.root).resolve())

    paths = sorted(
        path
        for path in _files_to_check(ctx.profile, scope)
        if path.suffix.lower() in (".tex", ".sty", ".cls")
    )
    found: dict[str, int] = {}
    fixed: dict[str, int] = {}
    files: dict[str, set[Path]] = {}
    skipped: list = []
    diffs: list[str] = []
    changed = 0
    for path in paths:
        try:
            findings = check_tagging(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for finding in findings:
            found[finding.rule] = found.get(finding.rule, 0) + 1
            files.setdefault(finding.rule, set()).add(path)
        if not findings:
            continue
        try:
            plan = plan_rewrites(path, skip=skip)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for rule, sites in plan.counts().items():
            fixed[rule] = fixed.get(rule, 0) + sites
        skipped.extend((path, item) for item in plan.skipped)
        if fix and plan.changed:
            changed += 1
            if write:
                plan.write()
            elif len(diffs) < 20:
                diffs.append(plan.diff())

    if ctx.as_json:
        ctx.emit(
            {
                "scope": scope,
                "files": len(paths),
                "rules": [
                    {
                        "rule": rule,
                        "sites": count,
                        "files": len(files.get(rule, ())),
                        "fixable": fixed.get(rule, 0),
                    }
                    for rule, count in sorted(found.items())
                ],
                "skipped": [
                    {"rule": item.rule, "file": str(path), "line": item.line,
                     "reason": item.reason}
                    for path, item in skipped
                ],
                "written": changed if write else 0,
            }
        )
        sys.exit(EXIT_OK if not found else EXIT_FINDINGS)

    console = ctx.console
    if not found:
        console.print(f"[green]No tagging blockers in {len(paths)} file(s).[/green]")
        sys.exit(EXIT_OK)

    table = Table(title=f"tagging tier — {len(paths)} file(s) scanned", title_justify="left")
    table.add_column("Rule", style="bold", no_wrap=True)
    table.add_column("Sites", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Auto-fixable", justify="right")
    for rule in sorted(found, key=lambda r: (RULES.index(r) if r in RULES else 99, r)):
        auto = fixed.get(rule, 0)
        if rule in skip:
            note = "[dim]handled by tagging=on[/dim]"
        elif auto:
            note = f"[green]{auto}[/green]"
        else:
            note = "[yellow]0[/yellow]"
        table.add_row(rule, str(found[rule]), str(len(files.get(rule, ()))), note)
    console.print(table)
    console.print(
        "\n[dim]These are latex-lab limitations, not WCAG or PDF/UA rules. "
        "`check` reports accessibility; this reports whether the source builds "
        "at all.[/dim]"
    )

    if skipped:
        # Named individually rather than counted. A rewriter that silently
        # declines is indistinguishable from one that has no cases left.
        console.print(f"\n[yellow]Left alone ({len(skipped)}):[/yellow]")
        seen: set[tuple[str, str]] = set()
        for path, item in skipped:
            key = (item.rule, item.reason)
            if key in seen:
                continue
            seen.add(key)
            console.print(f"  [dim]{item.rule}[/dim] {escape(item.reason)}")
            console.print(f"    [dim]first at {escape(str(path))}:{item.line}[/dim]")

    if fix and not write:
        for diff in diffs:
            console.print(escape(diff))
        console.print(
            f"\n[dim]{changed} file(s) would change; re-run with --write to apply[/dim]"
        )
    elif write:
        console.print(f"\n[green]{changed} file(s) rewritten.[/green]")
    else:
        console.print("\n[dim]re-run with --fix to see the rewrites[/dim]")
    sys.exit(EXIT_FINDINGS)


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
    scope = ctx.scope_or_here(scope)
    from .check.rules import Severity, check_log, check_pdf_structure, check_source
    from .check.vera import check_verapdf

    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings = []
    for path in sorted(_files_to_check(ctx.profile, scope)):
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
        # The authoritative gate runs last, so its object-numbered findings sit
        # under the source-level ones a person can act on directly.
        findings.extend(check_verapdf(Path(pdf)))

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
    from .run import RunConfig

    config = RunConfig.load(config_path) if config_path else RunConfig(profile=ctx.profile.name)
    if assignments:
        config = config.with_assignments(assignments)
    if output is not None:
        # `replace`, not a field-by-field rebuild: this used to restate five of
        # Output's six fields and silently drop the sixth, so a per-artifact
        # directory override set in the TUI vanished on any run that also
        # passed -o.
        config.output = replace(config.output, root=output)
    # Anchors a defaulted root to the corpus, so output never lands in whatever
    # directory the command happened to be run from -- the tool's own checkout
    # included. A root the user named is left alone.
    config.output.anchor(ctx.profile)
    config.write = write
    return config


def _report_table(reports: list) -> Table:
    table = Table(header_style="bold", title_justify="left")
    table.add_column("assignment", no_wrap=True)
    table.add_column("variant", no_wrap=True)
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
        if report.uncertain:
            # Built from a question another semester's bank supplied, and the
            # banks did not agree on it. Not a clean conversion of anything.
            mark = "[yellow]≈[/yellow]"
        elif report.ok:
            mark = "[green]✓[/green]"
        elif report.built:
            # Built, with something in the log worth reading. Not the same as
            # nothing coming out, and it used to be drawn the same way.
            mark = "[yellow]![/yellow]"
        else:
            mark = "[red]✗[/red]"
        table.add_row(
            report.assignment,
            report.variant,
            mark,
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
    help="Replay a run.yaml written by `latexally run`.",
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
    help="Write the PDF beside the original instead of into the output directory. Refuses on a dirty git worktree.",
)
@click.option(
    "--edit",
    is_flag=True,
    help=(
        "Rewrite the corpus .tex in place, so the folder builds with a bare "
        "pdflatex. Implies --in-place. Refuses on a dirty git worktree; undo "
        "with `latexally revert`."
    ),
)
@click.option(
    "--jobs",
    "-j",
    type=click.IntRange(1, 64),
    default=None,
    help="Documents to build at once. Default 1. Each is three LaTeX passes.",
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
    edit: bool,
    jobs: int | None,
    question_tags: bool,
    house_colors: bool,
    placeholders: bool,
) -> None:
    """Convert and build assignments. This is what `latexally run` runs.

    Every flag here corresponds to a screen in the interactive runner, and both
    paths end up calling the same engine — so a run can be explored in the TUI,
    saved, and replayed unchanged in CI.
    """
    from .build import build_run, describe_run
    from .run import AltChoice, ColorChoice

    # `--here` answers "which assignment" from the shell's own position, so a
    # bare `latexally --here build --write --edit` is the whole command.
    if not assignments and ctx.here_scope is not None:
        assignments = (ctx.here_scope,)
    config = _load_run_config(ctx, config_path, assignments, output, write)
    if edit or in_place:
        # `edit` wins: it is a superset, and asking for both is not a conflict.
        config.output = replace(
            config.output, write_mode="edit" if edit else "in-place"
        )
    if jobs is not None:
        config.jobs = jobs
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
    except LatexAllyError as exc:
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
                console.print(
                    f"  {report.assignment}  {report.variant}  ({report.driver})"
                )
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
            _report_substitutions(console, reports)
            _name_the_log(console, config)
    sys.exit(EXIT_FINDINGS if failures else EXIT_OK)


def _slug_for(report) -> str:
    from .build import _slug_for as slug_for

    return slug_for(report)


def _report_substitutions(console: Console, reports: list) -> None:
    """Say which documents contain a question the corpus could not supply."""
    repaired = [report for report in reports if report.substituted]
    if not repaired:
        return
    total = sum(len(report.substitutions) for report in repaired)
    unsure = [item for report in repaired for item in report.substitutions
              if item.ambiguous]
    console.print(
        f"\n[yellow]{total} missing include(s) stood in from elsewhere in the "
        f"corpus[/yellow] across {len(repaired)} document(s). The corpus was "
        "not modified."
    )
    for report in repaired:
        for item in report.substitutions:
            flag = "[yellow]DIFFERS[/yellow]" if item.ambiguous else "[dim]ok[/dim]"
            console.print(
                f"  {flag} {escape(item.wanted)}\n"
                f"        from {escape(str(item.used))}"
            )
    if unsure:
        console.print(
            f"\n[yellow]{len(unsure)} of them came from banks that DISAGREE[/yellow]"
            " — the stand-in may not be the question the assignment asked.\n"
            "[dim]Every substitution, its candidates and the fix are listed "
            "under SUBSTITUTED INCLUDES in build-log.txt.[/dim]"
        )


def _name_the_log(console: Console, config) -> None:
    """Say where the written account of this run is, once."""
    from .tui import show_path

    saved = config.output.root / "build-log.txt"
    if saved.is_file():
        console.print(f"\n[dim]written to {escape(show_path(saved))}[/dim]")


def _print_failures(console: Console, failures: list) -> None:
    """Say what went wrong, and where to look next.

    Shared by `build` and `run`: the interactive path used to show a ✗ in the
    table and nothing else, which tells a user that something failed and gives
    them no way to find out what.
    """
    for report in failures:
        if report.inherited and not report.regression:
            # The untouched source fails the same way. Saying so first is the
            # difference between "this tool broke your exam" and "this exam has
            # not compiled since 2015" -- opposite problems with opposite fixes,
            # and only one of them is worth reading the injected preamble for.
            console.print(
                f"\n[yellow]{escape(report.assignment)} "
                f"({escape(report.variant)}): the source does not compile "
                f"either; conversion did not cause this[/yellow]"
            )
        elif report.built:
            console.print(
                f"\n[yellow]{escape(report.assignment)} "
                f"({escape(report.variant)}) built, with "
                f"{len(report.errors)} error(s) in the log[/yellow]"
            )
            if report.pdf:
                from .tui import show_path

                console.print(f"  [dim]{escape(show_path(report.pdf))}[/dim]")
        else:
            console.print(
                f"\n[red]{escape(report.assignment)} "
                f"({escape(report.variant)}) failed — no PDF[/red]"
            )
        if report.note:
            console.print(f"  {escape(report.note)}")
        for line in report.errors[:5]:
            console.print(f"  [red]{escape(line)}[/red]")
        if len(report.errors) > 5:
            console.print(f"  [dim]…{len(report.errors) - 5} more[/dim]")
        if report.log:
            from .tui import show_path

            # The logs are one file per run now, so the path alone is not a
            # destination -- name the section banner to search for.
            console.print(
                f"  [dim]full log: {escape(show_path(report.log))}"
                f"  (search '=== {escape(_slug_for(report))}')[/dim]"
            )
    if failures:
        # Most build failures in this corpus are constructs LaTeX's own tagging
        # cannot handle, and `doctor --tagging` names the file and line in
        # milliseconds rather than after another three-minute compile.
        console.print(
            "\n[dim]`latexally doctor --tagging <scope>` locates constructs that "
            "tagging cannot compile, without rebuilding, and `--fix` rewrites "
            "the ones that can be rewritten.[/dim]"
        )


def _undo(
    ctx: Context,
    scope: str | None,
    output: Path | None,
    config_path: Path | None,
    write: bool,
    *,
    restore: bool,
    force: bool = False,
) -> None:
    """The body of both `clean` and `revert`.

    They differ by one argument. Keeping them one function is what stops
    `clean` from quietly diverging into a second, less careful implementation
    of the same deletion.
    """
    from .revert import do_revert, plan_revert
    from .tui.summary import show_path

    scope = ctx.scope_or_here(scope)
    config = _load_run_config(ctx, config_path, (), output, write)
    console = ctx.console
    try:
        plan = plan_revert(config, ctx.profile, scope, restore=restore, force=force)
        if write:
            do_revert(plan, verify=restore)
    except LatexAllyError as exc:
        if ctx.as_json:
            ctx.emit({"ok": False, "error": str(exc)})
        else:
            console.print(f"[red]error:[/red] {escape(str(exc))}")
        sys.exit(EXIT_ERROR)

    ctx.emit({"ok": True, "written": write, "restored": restore, **plan.as_dict()})
    if plan.empty and not plan.kept:
        console.print("[dim]nothing to do[/dim]")
        sys.exit(EXIT_OK)

    root = ctx.profile.corpus.root.resolve()

    def _listing(title: str, paths: list[Path], relative_to: Path | None) -> None:
        if not paths:
            return
        console.print(f"[bold]{title}[/bold] ({len(paths)})")
        for path in paths[:10]:
            shown: Path | str
            if relative_to is not None:
                try:
                    shown = path.relative_to(relative_to)
                except ValueError:
                    shown = show_path(path)
            else:
                shown = show_path(path)
            console.print(f"  {escape(str(shown))}")
        if len(paths) > 10:
            console.print(f"  [dim]…{len(paths) - 10} more[/dim]")

    tense = "" if write else "would be "
    _listing(f"{tense}restored with git".strip(), plan.restore, root)
    _listing(f"{tense}deleted".strip(), plan.remove, root)
    _listing(f"output {tense}removed".strip(), plan.outputs, None)
    if plan.kept:
        console.print(
            f"[bold]kept[/bold] ({len(plan.kept)}) — descriptions written by hand; "
            "git never had these, so they are not deleted. --force removes them"
        )
        for path in plan.kept[:10]:
            console.print(f"  {escape(str(path.relative_to(root)))}")

    if not write:
        console.print("\n[dim]dry run — pass --write to do it[/dim]")
    sys.exit(EXIT_OK)


@main.command()
@click.argument("scope", required=False)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    help="The output tree to delete. Defaults to <corpus>/ally-out.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    help="Read the artifact locations from the run.yaml that produced them.",
)
@click.option("--write", is_flag=True, help="Actually delete (default is a dry run).")
@click.option(
    "--force",
    is_flag=True,
    help="Also delete worklogs somebody has written descriptions into.",
)
@pass_context
def clean(
    ctx: Context,
    scope: str | None,
    output: Path | None,
    config_path: Path | None,
    write: bool,
    force: bool,
) -> None:
    """Delete what a run produced. Never touches your .tex.

    The residual worklogs, the `*-accessible.*` PDFs and logs, the
    `latexally-*.sty` installed beside a driver, and the output tree. Files this
    tool did not write are left alone, and so is every source file — including
    ones a previous `--edit` run modified. Use `revert` for those.

    Needs no git and no repository. A worklog somebody has written descriptions
    into is reported and kept; `--force` deletes it too.
    """
    _undo(ctx, scope, output, config_path, write, restore=False, force=force)


@main.command()
@click.argument("scope", required=False)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    help="The output tree to delete. Defaults to <corpus>/ally-out.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    help="Read the artifact locations from the run.yaml that produced them.",
)
@click.option("--write", is_flag=True, help="Actually revert (default is a dry run).")
@click.option(
    "--force",
    is_flag=True,
    help="Also delete worklogs somebody has written descriptions into.",
)
@pass_context
def revert(
    ctx: Context,
    scope: str | None,
    output: Path | None,
    config_path: Path | None,
    write: bool,
    force: bool,
) -> None:
    """Everything `clean` does, and restore the .tex a run rewrote.

    The restore is `git checkout`, so this needs the corpus to be a git
    repository and it refuses outside one. That is not a limitation to work
    around: `--edit` already refuses to start on a dirty worktree, so at the
    moment a revert runs, the modifications in scope are this tool's and
    nobody else's. If you have edited those files since, commit first — the
    checkout cannot tell your work from the tool's.

    Afterwards it re-reads `git status` and fails loudly rather than let a
    half-done revert read as a clean one.
    """
    _undo(ctx, scope, output, config_path, write, restore=True, force=force)


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
    from .tui import LatexAllyApp

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # Textual needs a terminal. Saying so, and naming the route that does not,
        # is the difference between a tool with a CI story and a traceback.
        click.echo(
            "latexally run needs a terminal.\n"
            "For CI or a pipe, replay a saved configuration instead:\n"
            "    latexally build --config ally-out/run.yaml --write",
            err=True,
        )
        sys.exit(EXIT_ERROR)

    config = _load_run_config(ctx, config_path, (), output, False)
    app = LatexAllyApp(ctx.profile, config)
    # Keyboard only. Textual's mouse tracking also swallows the terminal's own
    # click-drag text selection and scrollback, and every control in the runner
    # has a key, so there is nothing for a mouse to reach that a key cannot.
    app.run(mouse=False)

    if not app.should_run:
        ctx.console.print("[dim]nothing built[/dim]")
        sys.exit(EXIT_OK)

    # The app draws all of this while it runs, but Textual restores the terminal
    # on exit and takes the screen with it. Repeating it here is what leaves a
    # record in the scrollback -- and what a screen reader can go back over.
    console = ctx.console
    reports = app.reports
    console.print(_report_table(reports))
    descriptions = app.descriptions
    if descriptions.get("scanned") and descriptions.get("outstanding"):
        console.print(
            f"\n[bold]{descriptions['outstanding']}[/bold] figure(s) still need "
            f"alt text. Fill them in:"
        )
        for path in descriptions.get("worklogs", [])[:8]:
            console.print(f"  {escape(path)}")

    failures = [report for report in reports if not report.ok]
    _print_failures(console, failures)
    _report_substitutions(console, reports)
    _name_the_log(console, config)
    sys.exit(EXIT_FINDINGS if failures else EXIT_OK)


# ---------------------------------------------------------------------- #
# agent harness
# ---------------------------------------------------------------------- #


@main.group()
def agent() -> None:
    """Machine-readable interface for LLM agents and scripts."""


@agent.command("next-task")
@click.option(
    "--limit",
    "-n",
    type=int,
    default=1,
    help="How many tasks to return.",
)
@click.option("--genre", default=None, help="Restrict to one genre (circuit, plot, image…).")
@click.option("--refresh", is_flag=True, help="Re-scan the corpus before selecting.")
@pass_context
def agent_next_task(ctx: Context, limit: int, genre: str | None, refresh: bool) -> None:
    """Return self-contained description tasks, highest-value first."""
    from .agent import next_tasks

    tasks = next_tasks(
        ctx.profile,
        limit=limit,
        genre=genre,
        refresh=refresh,
        scope=ctx.here_scope,
    )
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
    from .agent import submit

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
    except LatexAllyError as exc:
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
    from .agent import AUTHORING_RULES

    if ctx.as_json:
        ctx.emit({"rules": list(AUTHORING_RULES)})
    else:
        for rule in AUTHORING_RULES:
            ctx.console.print(f"• {escape(rule)}")
    sys.exit(EXIT_OK)


if __name__ == "__main__":  # pragma: no cover
    main()
