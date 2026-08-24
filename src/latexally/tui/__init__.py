"""The interactive runner: pick a scope, pick your standards, pick where output goes.

Built on Rich rather than Textual, deliberately. Textual is declared as an extra
but is not installed, and an interactive tool that cannot start until you install
something is a tool people work around. Rich is already a hard dependency, works
over SSH and inside a CI log, and -- because every screen is a function from
(state, answer) to state -- the whole wizard is testable by feeding it a list of
strings, with no terminal and no pilot harness.

The wizard's only output is a :class:`~latexa11y.run.RunConfig`. It never builds
anything itself, so there is exactly one implementation of "what conversion
does", shared with ``latexa11y build`` and with any agent driving the CLI.

Two rules the screens follow:

1. **Every toggle shows its cost.** ``question_tags`` reflows one question in
   five, and a person turning it on is entitled to know that before the run
   rather than after, from the diff.
2. **Nothing is written until you say so.** The default is a dry run that prints
   the exact preamble it would inject and every path it would touch.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import Profile
from ..errors import LatexA11yError
from ..run import (
    ALT_MODES,
    ARTIFACTS,
    COLOR_MODES,
    STANDARD_TOGGLES,
    WRITE_MODES,
    VARIANT_LABELS,
    VARIANTS,
    Assignment,
    AltChoice,
    ColorChoice,
    Output,
    RunConfig,
    discover_assignments,
    group_by_kind,
    hex_to_rgb,
    normalise_hex,
)

__all__ = ["Wizard", "run_wizard"]

#: A prompt is any callable taking the question and returning the answer. The
#: real one reads the terminal; a test hands over a scripted list.
Prompt = Callable[[str], str]


def show_path(path: Path) -> str:
    """A path as short as it can be without becoming ambiguous.

    Paths are absolute internally, because a relative ``-output-directory``
    resolves against the subprocess's directory rather than the user's. Printing
    them raw puts a wrapped 80-character absolute path in every table, so
    anything under the working directory is shown relative to it and everything
    else is shown in full.
    """
    try:
        return str(path.absolute().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _scripted(answers: Sequence[str]) -> Prompt:
    """A prompt that replays a fixed list, then answers 'q' forever.

    The trailing 'q' matters: a test that under-supplies answers should end the
    wizard, not hang or raise StopIteration from somewhere unrelated.
    """
    remaining = list(answers)

    def ask(_: str) -> str:
        return remaining.pop(0) if remaining else "q"

    return ask


class Wizard:
    """Interactive editor for a :class:`RunConfig`."""

    def __init__(
        self,
        profile: Profile,
        config: RunConfig | None = None,
        *,
        console: Console | None = None,
        prompt: Prompt | None = None,
    ) -> None:
        self.profile = profile
        self.config = config or RunConfig(profile=profile.name)
        self.console = console or Console()
        # The question is printed through the console, never by the prompt, so
        # that everything the user sees goes through one channel -- which is
        # what lets a test read the prompts as well as the screens.
        self.prompt = prompt or (lambda _: input())
        self._assignments: list[Assignment] | None = None
        self.done = False
        self.should_run = False

    # ------------------------------------------------------------------ #
    # discovery, cached: walking 17k files per keystroke is not interactive
    # ------------------------------------------------------------------ #

    def assignments(self, scope: str | None = None) -> list[Assignment]:
        if self._assignments is None or scope is not None:
            self._assignments = discover_assignments(self.profile, scope)
        return self._assignments

    # ------------------------------------------------------------------ #
    # the summary screen
    # ------------------------------------------------------------------ #

    def summary(self) -> Panel:
        config = self.config
        selected = len(config.assignments)
        standards = self.config.standards

        marks = "  ".join(
            f"[{'green' if getattr(standards, t.key) else 'red'}]"
            f"{'✓' if getattr(standards, t.key) else '✗'}[/] {t.label.split('(')[0].strip()}"
            for t in STANDARD_TOGGLES
        )

        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="bold cyan", width=3)
        table.add_column(style="bold", width=12)
        table.add_column(overflow="fold")
        table.add_row("1", "Scope", self._describe_scope())
        table.add_row("2", "Standards", marks)
        table.add_row("3", "Colours", config.colors.describe(self.profile))
        table.add_row("4", "Alt text", config.alt.describe())
        table.add_row("5", "Output", self._describe_output())
        table.add_row("6", "Documents", self._describe_variants())

        footer = Text.from_markup(
            "[dim]1-6[/] edit    [dim]p[/] preview the plan    "
            "[dim]r[/] run    [dim]s[/] save run.yaml    [dim]q[/] quit"
        )
        ready = selected > 0
        if not ready:
            footer = Text.from_markup(
                "[yellow]Choose a scope first — press 1.[/]  [dim]q[/] quit"
            )
        return Panel(
            Group(table, Text(""), footer),
            title=f"latexa11y run — profile: [bold]{escape(self.profile.name)}[/]",
            title_align="left",
            border_style="cyan" if ready else "yellow",
        )

    def _describe_scope(self) -> str:
        count = len(self.config.assignments)
        if not count:
            return "[yellow]nothing selected[/]"
        shown = ", ".join(self.config.assignments[:3])
        more = f" +{count - 3} more" if count > 3 else ""
        return f"{count} assignment(s): {escape(shown)}{more}"

    def _describe_variants(self) -> str:
        chosen = self.config.variants
        if not chosen:
            return "every version each assignment has (solutions AND blank)"
        labels = dict(VARIANT_LABELS)
        return ", ".join(f"{name} ({labels.get(name, name)})" for name in chosen)

    def edit_variants(self) -> None:
        """Which documents of each assignment to build.

        An assignment is not one document. `sol9.tex` and `prob9.tex` pull in
        the same body and differ only in whether `\\sol` prints; discussions add
        a student handout and an answers-only build. Converting solutions alone
        leaves the file students actually receive untagged, so the default is
        everything.
        """
        available = self._available_variants()
        while True:
            self.console.print()
            table = Table(box=None, header_style="bold", padding=(0, 2))
            table.add_column("#", style="cyan", width=3)
            table.add_column("", width=2)
            table.add_column("document", no_wrap=True)
            table.add_column("", overflow="fold", style="dim")
            table.add_column("in scope", justify="right")
            for index, (name, note) in enumerate(VARIANT_LABELS, 1):
                on = not self.config.variants or name in self.config.variants
                count = available.get(name, 0)
                table.add_row(
                    str(index),
                    "[green]✓[/]" if on else "[red]✗[/]",
                    name,
                    note,
                    str(count) if count else "[dim]none[/]",
                )
            self.console.print(table)
            self.console.print(
                "[dim]A number toggles one. Everything selected is the same as "
                "nothing selected: build every version each assignment has.[/]"
            )
            self.console.print("[dim]number toggles   a all   blank returns[/]")

            answer = self.ask("documents> ").lower()
            if not answer or answer in ("q", "quit", "exit"):
                return
            if answer == "a":
                self.config.variants = ()
                continue
            try:
                name = VARIANTS[int(answer) - 1]
            except (ValueError, IndexError):
                self.console.print("[red]not one of the listed documents[/]")
                continue
            current = set(self.config.variants or VARIANTS)
            current.symmetric_difference_update({name})
            if not current:
                self.console.print(
                    "[yellow]That would build nothing.[/] Leave at least one."
                )
                continue
            ordered = tuple(item for item in VARIANTS if item in current)
            self.config.variants = () if set(ordered) == set(VARIANTS) else ordered

    def _available_variants(self) -> dict[str, int]:
        """How many selected assignments actually have each variant.

        Shown because the answer is course-specific and often surprising:
        homeworks have solutions and a blank version, discussions add an
        answers-only build, and a few assignments have only one file.
        """
        from ..run import iter_selected

        counts: dict[str, int] = {}
        try:
            for assignment in iter_selected(self.profile, self.config):
                for name in assignment.drivers:
                    counts[name] = counts.get(name, 0) + 1
        except LatexA11yError:
            pass
        return counts

    def _describe_output(self) -> str:
        output = self.config.output
        mode = (
            "[yellow]IN PLACE — edits the corpus[/]"
            if output.in_place
            else "mirror — corpus untouched"
        )
        return f"{escape(str(output.root))}/   ({mode})"

    # ------------------------------------------------------------------ #
    # the loop
    # ------------------------------------------------------------------ #

    def ask(self, question: str) -> str:
        """Put a question on screen and read the answer."""
        self.console.print(question, end="")
        return self.prompt(question).strip()

    def step(self) -> None:
        """Show the summary and handle exactly one command."""
        self.console.print(self.summary())
        self.dispatch(self.ask("> ").lower())

    def dispatch(self, command: str) -> None:
        actions = {
            "1": self.edit_scope,
            "2": self.edit_standards,
            "3": self.edit_colors,
            "4": self.edit_descriptions,
            "5": self.edit_output,
            "6": self.edit_variants,
            "p": self.preview,
            "r": self.confirm_run,
            "s": self.save,
        }
        if command in ("q", "quit", "exit"):
            self.done = True
            return
        action = actions.get(command)
        if action is None:
            if command:
                self.console.print(f"[red]unknown command {command!r}[/]")
            return
        try:
            action()
        except LatexA11yError as exc:
            self.console.print(f"[red]{escape(str(exc))}[/]")

    def loop(self) -> RunConfig:
        while not self.done:
            self.step()
        return self.config

    # ------------------------------------------------------------------ #
    # 1. scope
    # ------------------------------------------------------------------ #

    def edit_scope(self) -> None:
        named = sorted(self.profile.corpus.named)
        self.console.print("\n[bold]Named scopes[/] (from the profile)")
        for index, name in enumerate(named, 1):
            self.console.print(f"  [cyan]{index}[/] {name}")
        self.console.print(
            "  [cyan]p[/] a path relative to the corpus root "
            "(e.g. sp26/hw, or sp26/hw/9)"
        )
        answer = self.ask("scope> ")
        if not answer:
            return

        if answer == "p":
            raw = self.ask("path> ")
            if not raw:
                return
            found = self._discover(raw)
        else:
            try:
                scope = named[int(answer) - 1]
            except (ValueError, IndexError):
                self.console.print("[red]not one of the listed scopes[/]")
                return
            found = self._discover(scope)

        buildable = [item for item in found if item.buildable]
        skipped = len(found) - len(buildable)
        if not buildable:
            # Almost always the shared question bank, which is the first entry
            # in the alphabetical list and so the easiest to pick by accident.
            # Saying only "nothing here" invites the conclusion that the tool is
            # broken, when the scope is genuinely fragments rather than
            # documents -- and they get converted anyway, via the assignments
            # that include them.
            self.console.print(
                f"[yellow]No buildable assignments in that scope[/] — "
                f"{len(found)} director{'y' if len(found) == 1 else 'ies'} scanned, "
                "none containing a file with \\begin{document}."
            )
            self.console.print(
                "[dim]This is normal for a shared question bank: those files are "
                "\\input fragments, not documents. Convert the homeworks or "
                "discussions that include them and the questions come along.[/]"
            )
            return

        grouped = group_by_kind(buildable)
        self.console.print()
        table = Table(box=None, header_style="bold")
        table.add_column("#", style="cyan", width=3)
        table.add_column("kind")
        table.add_column("assignments", justify="right")
        kinds = list(grouped)
        for index, kind in enumerate(kinds, 1):
            table.add_row(str(index), kind, str(len(grouped[kind])))
        table.add_row("a", "[bold]all of the above[/]", str(len(buildable)))
        self.console.print(table)
        if skipped:
            # Never silently drop material: a directory with no \begin{document}
            # is usually a shared includes folder, but it might be a broken one.
            self.console.print(
                f"[dim]{skipped} director{'y' if skipped == 1 else 'ies'} skipped "
                "— no file containing \\begin{document}[/]"
            )

        choice = self.ask("kind (number, 'a', or blank to cancel)> ").lower()
        if not choice:
            return
        if choice == "a":
            chosen = buildable
        else:
            try:
                chosen = grouped[kinds[int(choice) - 1]]
            except (ValueError, IndexError, KeyError):
                self.console.print("[red]not one of the listed kinds[/]")
                return

        self.console.print(f"\n{len(chosen)} assignment(s):")
        for index, item in enumerate(chosen, 1):
            self.console.print(f"  [cyan]{index:>3}[/] {item.path}")
        picked = self.ask("select (blank = all, or 1,3,5-7)> ")
        selection = _parse_selection(picked, len(chosen))
        self.config = self.config.with_assignments(
            chosen[index - 1].path for index in selection
        )

    def _discover(self, scope: str) -> list[Assignment]:
        try:
            return discover_assignments(self.profile, scope)
        except LatexA11yError as exc:
            self.console.print(f"[red]{escape(str(exc))}[/]")
            return []

    # ------------------------------------------------------------------ #
    # 2. standards
    # ------------------------------------------------------------------ #

    def edit_standards(self) -> None:
        standards = self.config.standards
        self.console.print()
        table = Table(box=None, header_style="bold", padding=(0, 2))
        table.add_column("#", style="cyan", width=3)
        table.add_column("", width=2)
        table.add_column("standard")
        table.add_column("cost", style="yellow")
        for index, toggle in enumerate(STANDARD_TOGGLES, 1):
            on = getattr(standards, toggle.key)
            # The cost is shown whether the toggle is on or off. It is the price
            # of turning it ON, so hiding it while it is off hides it exactly
            # when someone is deciding whether to pay.
            table.add_row(
                str(index),
                "[green]✓[/]" if on else "[red]✗[/]",
                toggle.label,
                toggle.cost,
            )
        self.console.print(table)
        self.console.print("[dim]number toggles it, '?N' explains it, blank returns[/]")

        answer = self.ask("standards> ")
        while answer and answer not in ("q", "quit", "exit"):
            explain = answer.startswith("?")
            try:
                toggle = STANDARD_TOGGLES[int(answer.lstrip("?")) - 1]
            except (ValueError, IndexError):
                self.console.print("[red]not one of the listed standards[/]")
                return
            if explain:
                self.console.print(
                    Panel(
                        escape(toggle.detail),
                        title=f"{toggle.label} — cost: {toggle.cost}",
                        title_align="left",
                        border_style="dim",
                    )
                )
            else:
                standards.toggle(toggle.key)
                state = "on" if getattr(standards, toggle.key) else "off"
                self.console.print(f"  {toggle.label}: [bold]{state}[/]")
            answer = self.ask("standards> ")

    # ------------------------------------------------------------------ #
    # 3. colours
    # ------------------------------------------------------------------ #

    def _swatch(self, value: str | None) -> str:
        """A filled block in the colour itself, so the hex is not the only cue.

        A hex code is unreadable as a colour to most people and to all of us in
        a hurry. Rich paints the background, so the block shows the actual ink.
        """
        if not value:
            return "  "
        try:
            return f"[on {normalise_hex(value)}]  [/]"
        except LatexA11yError:
            return "[dim]??[/]"

    def _contrast(self, value: str | None) -> tuple[float | None, str]:
        """(ratio, rendered) against the profile's assumed page background."""
        if not value:
            return None, ""
        from ..check.contrast import contrast_ratio

        try:
            ratio = contrast_ratio(
                hex_to_rgb(value), hex_to_rgb(self.profile.colors.background)
            )
        except (LatexA11yError, ValueError):
            return None, "[dim]?[/]"
        floor = self.profile.colors.min_contrast_normal
        mark = "[green]✓[/]" if ratio >= floor else "[red]✗[/]"
        return ratio, f"{ratio:5.2f}:1 {mark}"

    def color_names(self) -> list[str]:
        """Every colour this run touches, profile order then any additions."""
        colors = self.profile.colors
        names = list(colors.replace) + [
            name for name in colors.originals if name not in colors.replace
        ]
        names += [name for name in self.config.colors.overrides if name not in names]
        return names

    def colors_table(self) -> Table:
        colors = self.profile.colors
        effective = self.config.colors.replacements(self.profile)

        table = Table(box=None, header_style="bold", padding=(0, 1))
        table.add_column("#", style="cyan", width=3)
        table.add_column("colour", no_wrap=True)
        table.add_column("course original", no_wrap=True)
        table.add_column("", width=2)
        table.add_column("contrast", justify="right")
        table.add_column("→", width=1)
        table.add_column("replacement", no_wrap=True)
        table.add_column("", width=2)
        table.add_column("contrast", justify="right")

        for index, name in enumerate(self.color_names(), 1):
            original = colors.originals.get(name)
            new = effective.get(name)
            customised = name in self.config.colors.overrides
            table.add_row(
                str(index),
                name + (" [cyan]*[/]" if customised else ""),
                original or "[dim]unknown[/]",
                self._swatch(original),
                self._contrast(original)[1],
                "→" if new else "",
                new or "[dim]kept[/]",
                self._swatch(new),
                self._contrast(new)[1],
            )
        return table

    def edit_colors(self) -> None:
        # The mode is offered even when the profile names no colours. It is not
        # only a lookup table: `conforming` emits \accesssetup{conforming-colors},
        # which remaps whatever the document itself defines. Returning early here
        # would make the choice unreachable for any course that has not yet
        # written its palette into a profile.
        while True:
            self.console.print()
            for index, mode in enumerate(COLOR_MODES, 1):
                marker = "[green]→[/]" if mode == self.config.colors.mode else " "
                note = (
                    "replace colours that fail contrast"
                    if mode == "conforming"
                    else "keep the course originals, contrast and all"
                )
                self.console.print(f" {marker} [cyan]{index}[/] {mode:<12} {note}")
            if self.color_names():
                self.console.print()
                self.console.print(self.colors_table())
                self.console.print(
                    f"[dim]Floor is {self.profile.colors.min_contrast_normal}:1 on "
                    f"{self.profile.colors.background} (WCAG 1.4.3 AA). "
                    "[/dim][cyan]*[/cyan][dim] marks a colour you set.[/dim]"
                )
                self.console.print(
                    "[dim]1-2 mode   c<N> set colour N to a hex you type   "
                    "r<N> reset N to the profile   blank returns[/]"
                )
            else:
                self.console.print(
                    "\n[dim]This profile names no colours, so there is nothing to "
                    "preview. `conforming` still applies: it remaps whatever the "
                    "document defines.[/]"
                )

            answer = self.ask("colours> ").lower()
            if not answer or answer in ("q", "quit", "exit"):
                return
            if answer.lower().startswith(("c", "r")):
                self._edit_one_color(answer)
                continue
            try:
                mode = COLOR_MODES[int(answer) - 1]
            except (ValueError, IndexError):
                self.console.print("[red]not one of the listed options[/]")
                continue
            self.config.colors = ColorChoice(
                mode=mode, overrides=self.config.colors.overrides
            )
            if mode == "house":
                self.console.print(
                    "[yellow]Note:[/] keeping the course palette can leave the "
                    "output failing WCAG 1.4.3 even when everything else conforms."
                )

    def _edit_one_color(self, command: str) -> None:
        action, _, digits = command[0].lower(), None, command[1:].strip()
        names = self.color_names()
        try:
            name = names[int(digits) - 1]
        except (ValueError, IndexError):
            self.console.print(f"[red]no colour numbered {digits!r}[/]")
            return

        if action == "r":
            self.config.colors.reset(name)
            self.console.print(f"  {name}: back to the profile's value")
            return

        current = self.config.colors.replacements(self.profile).get(name, "")
        value = self.ask(f"hex for {escape(name)} \\[{escape(current or 'none')}]> ")
        if not value:
            return
        try:
            normalised = normalise_hex(value)
        except LatexA11yError as exc:
            self.console.print(f"[red]{escape(str(exc))}[/]")
            return

        ratio, rendered = self._contrast(normalised)
        floor = self.profile.colors.min_contrast_normal
        self.console.print(
            f"  {name} → {normalised} {self._swatch(normalised)}  {rendered}"
        )
        if ratio is not None and ratio < floor:
            # Accepted, but never silently: the whole point of the conforming
            # mode is that its colours conform, and a chosen value that does not
            # would otherwise look identical to one that does.
            self.console.print(
                f"[yellow]  That is below the {floor}:1 floor.[/] It will be used "
                "as given; `latexa11y check` will report it."
            )
        self.config.colors.set(name, normalised)

    # ------------------------------------------------------------------ #
    # 4. descriptions
    # ------------------------------------------------------------------ #

    def edit_descriptions(self) -> None:
        notes = {
            "worklog": "find every figure and list it for someone to describe",
            "placeholders": "also mark each one in the .tex, where an author sees it",
            "off": "skip figures entirely",
        }
        self.console.print()
        for index, mode in enumerate(ALT_MODES, 1):
            marker = "→" if mode == self.config.alt.mode else " "
            self.console.print(f" {marker} [cyan]{index}[/] {mode:<13} {notes[mode]}")
        answer = self.ask("descriptions> ")
        if not answer:
            return
        try:
            mode = ALT_MODES[int(answer) - 1]
        except (ValueError, IndexError):
            self.console.print("[red]not one of the listed modes[/]")
            return

        strict = self.config.alt.strict
        if mode == "placeholders":
            self.console.print(
                Panel(
                    "A placeholder is written as [bold]<<TODO:figure-id>>[/], which "
                    "latexa11y-core refuses to accept as alt text. In strict mode "
                    "(the default) an unfilled one is a hard LaTeX [bold]error[/], so "
                    "the document cannot build — and therefore cannot ship a "
                    "placeholder to a screen reader as if it were a description.\n\n"
                    "Turning strict off lets a draft build with placeholders intact. "
                    "Never publish a document built that way.",
                    title="Placeholders",
                    title_align="left",
                    border_style="yellow",
                )
            )
            reply = self.ask("keep strict mode? [Y/n]> ").lower()
            strict = reply not in ("n", "no")
        self.config.alt = AltChoice(mode=mode, strict=strict)

    # ------------------------------------------------------------------ #
    # 5. output
    # ------------------------------------------------------------------ #

    def output_table(self) -> Table:
        output = self.config.output
        table = Table(box=None, header_style="bold", padding=(0, 1))
        table.add_column("#", style="cyan", width=3)
        table.add_column("artifact", no_wrap=True)
        table.add_column("path", overflow="fold")
        table.add_column("", overflow="fold", style="dim")

        table.add_row(
            "0", "[bold]Root[/]", escape(str(output.root)),
            "everything else hangs off this",
        )
        for index, (slug, label, note) in enumerate(ARTIFACTS, 1):
            customised = slug in output.paths
            table.add_row(
                str(index),
                label + (" [cyan]*[/]" if customised else ""),
                escape(show_path(output.path_for(slug))),
                note,
            )
        return table

    def edit_output(self) -> None:
        while True:
            output = self.config.output
            self.console.print()
            self.console.print(self.output_table())
            self.console.print(
                f"\n  write mode: [bold]{output.write_mode}[/]  "
                + (
                    "[yellow](edits your course sources)[/]"
                    if output.in_place
                    else "[dim](corpus stays read-only)[/]"
                )
            )
            self.console.print(
                "[dim]0-5 change a path (blank restores the default; a relative "
                "path hangs off the root)   w write mode   blank returns[/]"
            )

            answer = self.ask("output> ").lower()
            if not answer or answer in ("q", "quit", "exit"):
                return
            if answer == "w":
                self._edit_write_mode()
                continue
            try:
                index = int(answer)
            except ValueError:
                self.console.print("[red]not one of the listed options[/]")
                continue

            if index == 0:
                value = self.ask(f"root directory \\[{escape(str(output.root))}]> ")
                if value:
                    output.root = Path(value).expanduser()
                continue
            try:
                slug, label, _ = ARTIFACTS[index - 1]
            except IndexError:
                self.console.print("[red]not one of the listed options[/]")
                continue
            value = self.ask(f"{label} \\[{escape(show_path(output.path_for(slug)))}]> ")
            try:
                output.set_path(slug, value or None)
            except LatexA11yError as exc:
                self.console.print(f"[red]{escape(str(exc))}[/]")
                continue
            self.console.print(f"  {label}: {escape(show_path(output.path_for(slug)))}")

    def _edit_write_mode(self) -> None:
        output = self.config.output
        self.console.print()
        for index, mode in enumerate(WRITE_MODES, 1):
            marker = "[green]→[/]" if mode == output.write_mode else " "
            note = (
                "write converted .tex + PDFs to the output tree; corpus read-only"
                if mode == "mirror"
                else "edit the corpus .tex directly (requires a clean git worktree)"
            )
            self.console.print(f" {marker} [cyan]{index}[/] {mode:<9} {note}")
        answer = self.ask(f"write mode \\[{escape(output.write_mode)}]> ")
        if not answer:
            return
        try:
            mode = WRITE_MODES[int(answer) - 1]
        except (ValueError, IndexError):
            self.console.print("[red]not one of the listed modes[/]")
            return
        if mode == "in-place":
            self.console.print(
                "[yellow]In-place edits your course sources.[/] The run will refuse "
                "unless the corpus git worktree is clean, so the change stays "
                "reviewable and revertible."
            )
        output.write_mode = mode

    # ------------------------------------------------------------------ #
    # preview, save, run
    # ------------------------------------------------------------------ #

    def preview(self) -> None:
        from ..build import preamble_for, source_files_for
        from ..run import iter_selected

        if not self.config.assignments:
            self.console.print("[yellow]nothing selected[/]")
            return

        try:
            lines = preamble_for(self.config, self.profile)
        except LatexA11yError as exc:
            self.console.print(f"[red]{escape(str(exc))}[/]")
            return

        self.console.print(
            Panel(
                escape("\n".join(lines)) or "[dim]nothing — every standard is off[/]",
                title="Injected into each driver",
                title_align="left",
                border_style="cyan",
            )
        )

        table = Table(box=None, header_style="bold")
        table.add_column("assignment")
        table.add_column("document")
        table.add_column("driver")
        table.add_column(".tex used", justify="right")
        table.add_column("→ PDF", overflow="fold")
        for assignment in iter_selected(self.profile, self.config):
            used = str(len(source_files_for(assignment, self.profile)))
            variants = assignment.variants_for(self.config.variants)
            if not variants:
                table.add_row(assignment.path, "[red]none[/]", "", used, "")
                continue
            for variant, driver in variants.items():
                slug = assignment.path.replace("/", "-")
                if variant != "document":
                    slug = f"{slug}-{variant}"
                table.add_row(
                    assignment.path,
                    variant,
                    driver,
                    used,
                    show_path(self.config.output.pdf_dir() / f"{slug}.pdf"),
                )
        self.console.print(table)
        self.console.print(
            "[dim]'.tex used' counts every file the driver reaches, including "
            "questions \\input from the shared bank — which is where most figures "
            "live.[/]"
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or (self.config.output.root / "run.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.config.to_yaml(), encoding="utf-8")
        self.console.print(f"[green]saved[/] {escape(show_path(path))}")
        self.console.print(
            f"[dim]replay with:  latexa11y run --config {escape(show_path(path))}[/]"
        )
        return path

    def confirm_run(self) -> None:
        if not self.config.assignments:
            self.console.print("[yellow]nothing selected[/]")
            return
        self.preview()
        count = len(self.config.assignments)
        if self.config.output.in_place:
            question = (
                f"[yellow]Build {count} assignment(s) and EDIT YOUR COURSE SOURCES "
                f"in place?[/]"
            )
        else:
            question = (
                f"Build {count} assignment(s) into {self.config.output.root}? "
                f"[dim](your corpus is not modified)[/]"
            )
        self.console.print(f"\n{question}")
        # Spelled out rather than "[y/N]": that convention silently treats Enter
        # as no, and the first thing anyone does at an unfamiliar prompt is press
        # Enter. Saying which key does what costs one line.
        reply = self.ask("type [bold]y[/] to build, or Enter to cancel> ").lower()
        if reply not in ("y", "yes"):
            self.console.print("[dim]cancelled — nothing was written[/]")
            return
        self.config = replace(self.config, write=True)
        self.should_run = True
        self.done = True


def _parse_selection(text: str, total: int) -> list[int]:
    """Parse ``1,3,5-7`` into 1-based indices. Blank means everything.

    Out-of-range and malformed pieces are dropped rather than raising: this
    reads a human's typing, and the summary screen shows what was understood.
    """
    if not text.strip():
        return list(range(1, total + 1))
    chosen: list[int] = []
    for piece in text.replace(" ", "").split(","):
        if not piece:
            continue
        if "-" in piece:
            low, _, high = piece.partition("-")
            try:
                chosen.extend(range(int(low), int(high) + 1))
            except ValueError:
                continue
        else:
            try:
                chosen.append(int(piece))
            except ValueError:
                continue
    return [index for index in dict.fromkeys(chosen) if 1 <= index <= total]


def run_wizard(
    profile: Profile,
    config: RunConfig | None = None,
    *,
    console: Console | None = None,
    answers: Sequence[str] | None = None,
) -> tuple[RunConfig, bool]:
    """Drive the wizard. Returns ``(config, should_run)``.

    ``answers`` replaces the terminal with a script, which is how this is tested.
    """
    wizard = Wizard(
        profile,
        config,
        console=console,
        prompt=_scripted(answers) if answers is not None else None,
    )
    wizard.loop()
    return wizard.config, wizard.should_run
