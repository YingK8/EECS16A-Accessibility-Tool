r"""The interactive runner, as a Textual app.

Built on Textual rather than hand-rolled menus, and the reason is not taste. The
previous front-end drew through Rich's ``Live``, which clips at the terminal
height: a scope of sixteen assignments filled the screen, the footer that said
``space toggle`` was cut off, and every row past the fold was unreachable. There
was no scrolling to reach them with. "How do I tick a box?" had no answer on
screen because the answer had been clipped off it.

So: every list here is a real scrolling widget, the footer is always visible and
always shows the real bindings, and the step that cannot be defaulted refuses to
advance with a **disabled Next button that says why**. Pressing Enter with
nothing chosen used to return ``None``, which the caller read as cancel and
silently left the screen -- the wrong answer to "you have not chosen anything
yet".

The app is a sequence of steps, each a :class:`StepScreen`:

    Scope → Documents → Standards → Colours → Alt text → Output → Review → Build

Two rules hold across all of them:

1. **The config is mutated as controls change**, never on the way out. Back is
   therefore lossless, and there is no "apply" to forget.
2. **A step with one possible answer is not a question.** A scope with one kind
   does not ask which kind; a kind with one assignment ticks it and says so.

The app's only product is a :class:`~latexally.run.RunConfig` and the reports
from :func:`~latexally.build.build_run`. It never converts anything itself, so
there is exactly one implementation of "what a run does", shared with
``latexally build`` and with any agent driving the CLI.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterator

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import Screen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    LoadingIndicator,
    OptionList,
    RadioButton,
    RadioSet,
    RichLog,
    Rule,
    SelectionList,
    Static,
)
from textual.widgets.data_table import CellDoesNotExist
from textual.widgets.option_list import OptionDoesNotExist
from textual.css.query import NoMatches
from textual.widgets.selection_list import Selection

from ..config import Profile
from ..errors import LatexAllyError
from ..run import (
    ALT_MODES,
    ARTIFACTS,
    STANDARD_TOGGLES,
    WRITE_MODES,
    AltChoice,
    RunConfig,
    normalise_hex,
)
from ..discover import (
    DEFAULT_VARIANTS,
    scope_from_cwd,
    VARIANT_LABELS,
    VARIANTS,
    Assignment,
    discover_assignments,
    group_by_kind,
    newest_first,
)
from .summary import (
    COLOR_COLUMNS,
    available_variants,
    color_names,
    color_rows,
    colors_table,
    contrast,
    describe_output,
    describe_variants,
    floor_for,
    output_table,
    proposal_for,
    show_path,
    swatch,
    under,
)

__all__ = [
    "Checklist",
    "Choice",
    "LatexAllyApp",
    "ModeScreen",
    "Radio",
    "RevertScreen",
]

def _plural(count: int) -> str:
    return "y" if count == 1 else "ies"


class Checklist(SelectionList):
    """A selection list that says what it means in characters, not in shades.

    Textual draws both tick states as the same ``X``, leaving colour to carry
    the difference. Two shades of one glyph is not a distinction anyone should
    have to squint at, and it is no distinction at all to a reader who cannot
    see the difference between them. So: ``[x]`` versus ``[ ]``.

    The cursor is reverse video on the whole row (see the app's CSS), which the
    tick inherits because it is drawn in the row's own style.
    """

    #: ``"[x] "``
    GUTTER = 4

    def _get_left_gutter_width(self) -> int:
        return self.GUTTER

    def render_line(self, y: int) -> Strip:
        line = OptionList.render_line(self, y)
        _, scroll_y = self.scroll_offset
        index = scroll_y + y
        try:
            selection = self.get_option_at_index(index)
        except OptionDoesNotExist:
            return line
        highlighted = self.highlighted == index
        style = next((segment.style for segment in line), None) or self.rich_style
        tick = "[x] " if selection.value in self._selected else "[ ] "
        strip = Strip([Segment(tick, style=style), *line])
        # Applied here rather than through `option-list--option-highlighted`:
        # get_visual_style() merges the component classes but drops their
        # text-style, so the CSS reverse never reached the rendered row.
        return strip.apply_style(Style(reverse=True)) if highlighted else strip


class Choice(RadioSet):
    """A radio group where ``↑`` ``↓`` choose rather than shop around.

    Textual's arrows move a second cursor over the options and leave Enter to
    commit, so ``(•)`` marks the value and something else marks where you are.
    Two cursors for one decision between two options is one too many: here the
    arrows move the value, and ``(•)`` is the only thing to read.
    """

    BINDINGS = [
        Binding("up,left", "step(-1)", "Choose", show=False),
        Binding("down,right", "step(1)", "Choose", show=False),
    ]

    def action_step(self, delta: int) -> None:
        buttons = list(self.query(Radio))
        if not buttons:
            return
        index = next((i for i, button in enumerate(buttons) if button.value), 0)
        buttons[(index + delta) % len(buttons)].value = True


class Radio(RadioButton):
    """``(•)`` / ``( )``, for the same reason :class:`Checklist` exists."""

    BUTTON_LEFT = "("
    BUTTON_INNER = "•"
    BUTTON_RIGHT = ")"

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        return Content.assemble(
            (self.BUTTON_LEFT, style),
            (self.BUTTON_INNER if self.value else " ", style),
            (self.BUTTON_RIGHT, style),
        )


# ---------------------------------------------------------------------- #
# the shared step frame
# ---------------------------------------------------------------------- #


class StepScreen(Screen):
    """One step: a heading, a body, and a footer that always names the keys.

    There are no buttons anywhere in this app. With the mouse off, a button is
    a worse key hint than the words ``n next`` -- it occupies three rows, it
    needs focus to activate, and it says nothing about which key reaches it.
    Textual's ``Footer`` already lists every binding and greys out the ones
    :meth:`check_action` reports as unavailable, so Next-disabled-with-a-reason
    lives there plus one line of prose in ``#reason``.
    """

    heading = ""
    hint = ""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("b", "back", "Back", show=False),
        # Enter is the key people press to go on, on every step. Priority,
        # because the focused widget would otherwise swallow it -- an
        # OptionList binds Enter to "tick this row". Ticking is `space`, which
        # SelectionList binds for it anyway, and editing a colour is `e`.
        Binding("enter", "advance", "Next", priority=True),
        Binding("n", "next", "Next", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._can_next = True

    @property
    def profile(self) -> Profile:
        return self.app.profile

    @property
    def config(self) -> RunConfig:
        return self.app.config

    def container(self) -> Widget:
        return Vertical(id="body")

    def body(self) -> Iterator[Widget]:
        return iter(())

    def compose(self) -> ComposeResult:
        steps = self.app.active_steps
        position = steps.index(type(self)) + 1
        yield Static(
            f"latexally run   {position}/{len(steps)}   {self.heading}",
            classes="heading",
        )
        if self.hint:
            yield Static(self.hint, classes="hint")
        with self.container():
            yield from self.body()
        yield Static("", id="reason", classes="reason")
        yield Footer()

    # -- flow ----------------------------------------------------------- #

    @property
    def first(self) -> bool:
        return self.app.active_steps.index(type(self)) == 0

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "back":
            # The first step has nowhere to go back to. Quitting is `q`.
            return False if self.first else True
        if action == "next":
            # None greys it in the footer; False would drop it, and "why can I
            # not go on" is exactly the question the footer should answer.
            return True if self._can_next else None
        if action == "advance":
            # In a text field Enter is the field's own -- submitting a typed
            # path, committing a hex -- and a priority binding that stole it
            # would make those fields impossible to use.
            if isinstance(self.app.focused, Input):
                return False
            return True if self._can_next else None
        return True

    def action_back(self) -> None:
        if self.first:
            return
        self.app.step(-1)

    def action_next(self) -> None:
        if not self._can_next:
            return
        self.app.step(1)

    def action_advance(self) -> None:
        """Enter. Same step, separate action so screens can decline the key."""
        self.action_next()

    def say(self, selector: str, text: str = "") -> None:
        """Update a note, and give its row back when there is nothing to say.

        Rows are the scarce resource on the terminal this rewrite exists for; a
        permanently blank line is one fewer directory visible.
        """
        widget = self.query_one(selector, Static)
        widget.update(Content(text))
        widget.display = bool(text)

    def set_next(self, ok: bool, reason: str = "") -> None:
        self._can_next = ok
        self.say("#reason", "" if ok else reason)
        self.refresh_bindings()


class ListStepScreen(StepScreen):
    """A step whose answer is a :class:`Checklist`."""

    BINDINGS = [
        Binding("a", "select_all", "All"),
        Binding("c", "select_none", "Clear"),
    ]
    list_id = "choices"

    def count_line(self) -> Iterator[Widget]:
        """One line saying how much is ticked. Empty lines take their row back."""
        line = Static("", id="count", classes="count")
        line.display = False
        yield line

    @property
    def choices(self) -> SelectionList:
        return self.query_one(f"#{self.list_id}", SelectionList)

    def action_select_all(self) -> None:
        self.choices.select_all()

    def action_select_none(self) -> None:
        self.choices.deselect_all()


# ---------------------------------------------------------------------- #
# 1. local or choose
# ---------------------------------------------------------------------- #


class ModeScreen(StepScreen):
    """Local, or pick from the corpus. Asked before anything is listed.

    This is one question with two words in it, on a screen of its own, and both
    facts are deliberate. It used to sit at the top of the scope screen above a
    list of sixty-two directories -- so the list was the first thing read, the
    question the second, and the arrows that were supposed to answer it went to
    the list instead, which had taken the focus.

    "Local" rather than "everything": the choice is about *where*, and
    "everything" reads as a quantity, as though the other option converted less
    of the same thing. Both options convert everything they cover.
    """

    heading = "Which material?"
    hint = "The folder you started in is the usual answer."
    AUTO_FOCUS = "#scope-mode"

    def body(self) -> Iterator[Widget]:
        here = self.app.here_scope
        with Horizontal(classes="row"):
            yield Static("Where  ↑ ↓", classes="gutter")
            yield Choice(
                Radio("", value=here is not None, name="local", id="local-radio"),
                Radio(
                    "choose — pick directories from anywhere in the corpus",
                    value=here is None,
                    name="choose",
                ),
                id="scope-mode",
            )
        yield Static("", id="mode-note", classes="note")

    def on_mount(self) -> None:
        here = self.app.here_scope
        radio = self.query_one("#local-radio", Radio)
        if here is None:
            # Nothing to offer, and saying which directory is not the corpus is
            # more use than a greyed row with no reason beside it.
            radio.label = "local — unavailable: you are not inside the corpus"
            radio.disabled = True
        else:
            radio.label = f"local — {here or 'the whole corpus'}"
        self._sync()

    @on(RadioSet.Changed, "#scope-mode")
    def _changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        pressed = self.query_one("#scope-mode", RadioSet).pressed_button
        mode = pressed.name if pressed else "choose"
        if self.app.here_scope is None:
            mode = "choose"
        self.app.scope_mode = mode
        self.say(
            "#mode-note",
            "The next step lists what that folder holds; you can untick any of it."
            if mode == "local"
            else "The next step opens the corpus to browse.",
        )


# ---------------------------------------------------------------------- #
# 2. scope
# ---------------------------------------------------------------------- #


class ScopeScreen(ListStepScreen):
    """The one question the tool cannot answer for you, asked first.

    One filter above the list: the named scopes the profile declares, walked
    with the arrows whose glyphs are printed in the row's own gutter. It is
    text, not widgets -- a row of buttons was unreachable without a mouse and,
    being mounted asynchronously, went stale the moment the scope changed
    underneath it.

    There was a second row for *kind* (homework, discussion, note). It is gone:
    scope is a glob over the corpus and kind is what a directory turned out to
    be, which filter the same axis from two directions -- the profile declares
    both a ``homeworks`` scope and a ``homework`` kind. One list holding
    everything in the scope makes "two discussions and one homework" a plain
    selection rather than something assembled across views. What each scope
    holds is still said, as a count, above the list.

    Nothing starts ticked. Starting with everything ticked made the common
    mistake silent: a scope of forty directories would build all forty because
    Next was the obvious key, and the run was well under way before anyone
    noticed.
    """

    heading = "What do you want to convert?"
    hint = (
        "Starts on the folder you ran this from. Next: documents, standards, "
        "colours, output."
    )
    list_id = "assignments"

    BINDINGS = [
        # priority, because a SelectionList is a ScrollView and binds the bare
        # arrows to a horizontal scroll it never needs -- which swallowed these
        # before they ever reached the screen.
        Binding("left", "scope(-1)", "Scope", priority=True),
        Binding("right", "scope(1)", "Scope", show=False, priority=True),
    ]

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Claim the arrows only where they mean "other scope" / "other kind".

        A footer listing a key that does nothing is worse than one listing
        nothing: it sends you looking for a row you were promised. And in the
        path field the arrows are the text caret, which outranks all of this.
        """
        if action == "scope":
            # In the path field the arrows are the text caret, which outranks
            # anything this screen wants them for. And in "everything here"
            # mode the scope row is not on screen at all, so advertising a key
            # that walks it sends someone looking for a row they were promised.
            typing = isinstance(self.app.focused, Input)
            if typing or not self._scopes:
                return False
            # Keyed on whether the row is actually on screen, not on the mode:
            # a local scan that finds nothing buildable puts the row back, and
            # the key that walks it has to come back with it.
            try:
                return True if self.query_one("#scope-line").display else False
            except NoMatches:
                # Called once before the row is mounted.
                return True
        return super().check_action(action, parameters)

    def __init__(self) -> None:
        super().__init__()
        self._scopes: list[str] = []
        self._scope_index: int | None = None
        self._scope = ""
        self._buildable: list[Assignment] = []
        #: Every ticked path, from every scope visited. The list only ever
        #: shows one scope, so its own selection cannot be the answer: moving
        #: from sp26 to exams would silently drop the sp26 picks.
        self._chosen: set[str] = set()
        #: What the list is showing right now, so a change can be attributed to
        #: this scope and leave every other scope's ticks alone.
        self._showing: list[str] = []
        self._rebuilding = False

    def body(self) -> Iterator[Widget]:
        with Horizontal(classes="row", id="scope-line"):
            yield Static("Scope  ← →", classes="gutter")
            yield Static("", id="scope-row", classes="rowtext")
        with Horizontal(classes="row", id="scope-path-line"):
            yield Static("Path   type", classes="gutter")
            yield Input(
                placeholder="a path relative to the corpus root, e.g. sp26/hw/9",
                id="scope-path",
                compact=True,
            )
        yield Static("", id="scope-note", classes="note")
        yield from self.count_line()
        scanning = LoadingIndicator(id="scanning")
        scanning.display = False
        yield scanning
        yield Checklist(id="assignments")
        yield Static("", id="pick-note", classes="note")
        yield Static("", id="elsewhere", classes="count")

    @property
    def _here(self) -> bool:
        """Is this run scoped to the folder the runner was started from?

        Answered on the previous step, not here: the question "which folder"
        must be settled before a list of sixty-two directories is drawn, or the
        list is the first thing read and the question the second.
        """
        return self.app.scope_mode == "local"

    def _apply_mode(self, browsing: bool | None = None) -> None:
        """Show the browsing controls only when they are of use.

        In `local` mode the scope row and the path field are two rows of chrome
        above an answer that is already correct, and on a short terminal they
        are two rows the list does not get.

        ``browsing`` forces them back on. It is passed when a local scan comes
        back with nothing buildable, because that screen was otherwise a dead
        end: an empty list, a disabled Next, `a` and `c` with nothing to act
        on, and no control on screen able to widen the scope. It looked exactly
        like a hung program.
        """
        here = self._here if browsing is None else not browsing
        self.query_one("#scope-line").display = bool(self._scopes) and not here
        self.query_one("#scope-path-line").display = not here
        self.refresh_bindings()
        if browsing is not None:
            return
        if here:
            self.scan(self.app.here_scope or "")
        elif not self._scopes:
            self.scan("")
        else:
            self.select_scope(self._scope_index or 0)

    def on_mount(self) -> None:
        self._scopes = list(self.profile.corpus.named)
        # A config replayed from run.yaml already names its directories. Seeding
        # the list from them is what makes `latexally run --config run.yaml`
        # open on what was saved -- and it is why the opening scan is skipped:
        # scanning clears the selection.
        self._chosen = set(self.config.assignments)
        self._rebuilding = True
        self.choices.add_options(
            [
                Selection(Content(path), path, True)
                for path in sorted(self._chosen, key=newest_first)
            ]
        )
        self._rebuilding = False
        self._render_rows()
        self._sync()
        if self._chosen:
            # A replayed config skips the opening scan, so nothing has focused
            # the list yet -- and the path field would otherwise take the focus
            # and swallow every letter key as text.
            self.query_one("#scope-line").display = bool(self._scopes)
            self.choices.focus()
        else:
            self._apply_mode()

    # -- the two filter rows -------------------------------------------- #

    def action_scope(self, delta: int) -> None:
        if not self._scopes:
            return
        index = 0 if self._scope_index is None else self._scope_index + delta
        self.select_scope(index % len(self._scopes))

    def select_scope(self, index: int) -> None:
        self._scope_index = index
        scope = self._scopes[index]
        self.query_one("#scope-path", Input).value = scope
        self.scan(scope)

    @on(Input.Submitted, "#scope-path")
    def _submitted_path(self, event: Input.Submitted) -> None:
        typed = event.value.strip()
        # A typed path that happens to name a scope keeps the row in step.
        self._scope_index = (
            self._scopes.index(typed) if typed in self._scopes else None
        )
        self.scan(typed)

    def _render_rows(self) -> None:
        self.query_one("#scope-row", Static).update(
            Content.from_markup(
                " ".join(
                    f"[reverse] {name} [/]" if index == self._scope_index
                    else f" {name} "
                    for index, name in enumerate(self._scopes)
                )
            )
        )

    # -- discovery ------------------------------------------------------- #

    def scan(self, scope: str) -> None:
        """Start a scan. The answer arrives in :meth:`_scanned`.

        ``discover_assignments`` globs every ``.tex`` in the corpus and, for any
        directory that does not follow the filename convention, reads each file
        looking for ``\begin{document}``. That is seconds, not milliseconds, so
        it cannot run on the message loop: the app would open frozen.
        """
        self._scope = scope
        # Empty the list and put the spinner over it. A scan can take seconds,
        # and leaving the previous scope's directories on screen the whole time
        # invites ticking one that is about to disappear.
        self._buildable = []
        self._showing = []
        self._rebuilding = True
        self.choices.clear_options()
        self._rebuilding = False
        # Beside the emptied list, not over it. `widget.loading` swaps the
        # list out for the spinner, and Textual then refuses it focus and
        # hands it to the path field -- where every letter is text, so the
        # footer emptied for the length of the scan.
        self.query_one("#scanning").display = True
        self.choices.focus()
        self.say("#scope-note", f"scanning {scope or 'the corpus'}…")
        self.say("#pick-note", "")
        self._render_rows()
        self._sync()
        # "0 of 0 selected" is not a fact about anything yet.
        self.say("#count", "")
        self._discover(scope)

    @work(thread=True, exclusive=True)
    def _discover(self, scope: str) -> None:
        try:
            found = discover_assignments(self.profile, scope)
            error = ""
        except LatexAllyError as exc:
            found, error = [], str(exc)
        self.app.call_from_thread(self._scanned, scope, found, error)

    def _scanned(self, scope: str, found: list, error: str) -> None:
        if scope != self._scope:  # a later scan overtook this one
            return
        notes = [error] if error else []
        buildable = [item for item in found if item.buildable]
        skipped = len(found) - len(buildable)
        if not buildable:
            # Usually the shared question bank. Saying only "nothing here"
            # invites the conclusion that the tool is broken, when the scope is
            # genuinely fragments rather than documents -- and they get
            # converted anyway, via the assignments that include them.
            notes.append(
                f"No buildable assignments in that scope — {len(found)} "
                f"director{_plural(len(found))} scanned, none containing a file "
                "with \\begin{document}."
            )
            notes.append(
                "This is normal for a shared question bank: those files are "
                "\\input fragments, not documents. Convert the homeworks or "
                "discussions that include them and the questions come along."
            )
        elif skipped:
            # Never silently drop material: a directory with no \begin{document}
            # is usually a shared includes folder, but it might be a broken one.
            notes.append(
                f"{skipped} director{_plural(skipped)} skipped — no file "
                "containing \\begin{document}"
            )

        self._buildable = buildable
        if buildable:
            # What the scope turned out to hold, said rather than made into a
            # second filter. Kind and scope narrow the same axis -- the profile
            # declares both a `homeworks` scope and a `homework` kind -- so one
            # of them had to be a sentence instead of a control.
            grouped = group_by_kind(buildable)
            notes.append(
                f"{len(buildable)} director{_plural(len(buildable))} — "
                + ", ".join(f"{len(items)} {kind}" for kind, items in grouped.items())
            )
        if not buildable and self._here:
            # A dead end otherwise: nothing to tick and nothing on screen that
            # could widen the scope. Give the controls back rather than leave
            # the only way out unmentioned.
            self._apply_mode(browsing=True)
        self.refresh_bindings()
        self.say("#scope-note", "\n".join(notes))
        self.refresh_list()

    # -- the list --------------------------------------------------------- #

    def refresh_list(self) -> None:
        self.query_one("#scanning").display = False
        items = self._buildable
        only = len(items) == 1
        if only:
            self._chosen.add(items[0].path)
        if self._here:
            # "Everything here" means everything here. Ticked rather than
            # merely implied, so the list still shows exactly what will be
            # built and any one of them can still be unticked.
            self._chosen.update(item.path for item in items)
        self._showing = [item.path for item in items]
        # Rebuilding the options fires SelectedChanged for each one; without
        # this the handler would read a half-built list as the user's answer.
        self._rebuilding = True
        self.choices.clear_options()
        self.choices.add_options(
            [
                Selection(
                    Content(
                        f"{item.path}   "
                        f"({len(item.drivers) or 1} document(s), "
                        f"{item.tex_files} .tex)"
                    ),
                    item.path,
                    item.path in self._chosen,
                )
                for item in items
            ]
        )
        # clear_options() leaves nothing highlighted, and Enter toggles the
        # highlighted row -- so without this the first Enter after a rescan
        # did nothing at all.
        self.choices.highlighted = 0 if items else None
        self._rebuilding = False
        self.say(
            "#pick-note", f"{items[0].path} — the only assignment here" if only else ""
        )
        if items:
            # Where the next action is. It also puts `a` and `c` on the list
            # rather than in the path field, where they would just be letters.
            self.choices.focus()
        self._sync()

    @on(SelectionList.SelectedChanged, "#assignments")
    def _selection_changed(self) -> None:
        if self._rebuilding:
            return
        # Only the scope on screen is being answered; every other one stands.
        self._chosen.difference_update(self._showing)
        self._chosen.update(self.choices.selected)
        self._sync()

    def _sync(self) -> None:
        # Newest first here too, so run.yaml and the build follow the order
        # the list was picked in rather than flipping back to alphabetical.
        self.app.config = self.config.with_assignments(
            sorted(self._chosen, key=newest_first)
        )
        here = set(self._showing) & self._chosen
        elsewhere = sorted(self._chosen - set(self._showing), key=newest_first)
        total = len(self._showing) or len(self._chosen)
        self.say(
            "#count",
            f"{len(here)} of {total} selected"
            + (f", {len(elsewhere)} in another scope" if elsewhere else ""),
        )
        # Named, not just counted. A tick you cannot see is a tick you cannot
        # take back, and it is still going to be built.
        shown = ", ".join(elsewhere[:3]) + (
            f" +{len(elsewhere) - 3} more" if len(elsewhere) > 3 else ""
        )
        self.say("#elsewhere", f"also ticked: {shown}" if elsewhere else "")
        self._render_rows()
        # Two different situations, and one message for both was misleading:
        # "tick at least one" is useless advice when there is nothing to tick.
        if self._chosen:
            reason = ""
        elif self._showing:
            reason = "Tick at least one directory — nothing is selected yet."
        else:
            reason = (
                "Nothing here to convert. Type another path above, or press "
                "esc to go back and choose."
            )
        self.set_next(bool(self._chosen), reason)


# ---------------------------------------------------------------------- #
# 2. documents
# ---------------------------------------------------------------------- #


class DocumentsScreen(ListStepScreen):
    r"""Which documents of each assignment to build.

    An assignment is not one document. ``sol9.tex`` and ``prob9.tex`` pull in
    the same body and differ only in whether ``\sol`` prints; discussions add a
    student handout and an answers-only build. Converting solutions alone leaves
    the file students actually receive untagged, so the default is everything.
    """

    heading = "Which documents of each assignment?"
    hint = (
        "Solutions and the blank handout by default — the two students are "
        "given. Answers-only is for staff marking; tick it if you want it."
    )
    list_id = "variants"

    def body(self) -> Iterator[Widget]:
        available = available_variants(self.profile, self.config)
        chosen = self.config.variants
        yield from self.count_line()
        yield Checklist(
            *(
                Selection(
                    Content(
                        f"{name}   {note} — "
                        + (
                            f"{available[name]} in scope"
                            if available.get(name)
                            else "none in scope"
                        )
                    ),
                    name,
                    name in chosen if chosen else name in DEFAULT_VARIANTS,
                )
                for name, note in VARIANT_LABELS
            ),
            id="variants",
        )
        yield Static("", id="variants-note", classes="note")

    def on_mount(self) -> None:
        self._sync()

    @on(SelectionList.SelectedChanged, "#variants")
    def _selection_changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        chosen = tuple(
            name for name, _ in VARIANT_LABELS if name in set(self.choices.selected)
        )
        if chosen:
            # The default set is stored as no filter, so ticking back to it
            # returns to the default rather than freezing today's list.
            self.config.variants = (
                () if set(chosen) == set(DEFAULT_VARIANTS) & set(VARIANTS) else chosen
            )
        self.say("#variants-note", describe_variants(self.config))
        self.set_next(
            bool(chosen),
            "That would build nothing. Leave at least one document ticked.",
        )


# ---------------------------------------------------------------------- #
# 3. standards
# ---------------------------------------------------------------------- #


class StandardsScreen(ListStepScreen):
    """Which accessibility standards this run applies.

    No predicted cost next to the toggle. A number like "~2.6% of pixels move"
    is an average of somebody else's documents; the build measures *your* pages
    against their untouched originals and reports that per document instead.
    """

    heading = "Which standards should this run apply?"
    hint = "The build measures what each one actually cost, page by page."
    list_id = "standards"

    def body(self) -> Iterator[Widget]:
        standards = self.config.standards
        yield from self.count_line()
        yield Checklist(
            *(
                Selection(
                    Content(toggle.label),
                    toggle.key,
                    getattr(standards, toggle.key),
                )
                for toggle in STANDARD_TOGGLES
            ),
            id="standards",
        )
        yield Static("", id="detail", classes="detail")

    def on_mount(self) -> None:
        self._detail(STANDARD_TOGGLES[0].key)
        self._sync()

    @on(SelectionList.SelectedChanged, "#standards")
    def _selection_changed(self) -> None:
        self._sync()

    @on(SelectionList.SelectionHighlighted, "#standards")
    def _highlighted(self, event: SelectionList.SelectionHighlighted) -> None:
        self._detail(event.selection.value)

    def _detail(self, key: str) -> None:
        toggle = next(t for t in STANDARD_TOGGLES if t.key == key)
        self.query_one("#detail", Static).update(
            Content(f"{toggle.label}\n\n{toggle.detail}")
        )

    def _sync(self) -> None:
        chosen = set(self.choices.selected)
        standards = self.config.standards
        for toggle in STANDARD_TOGGLES:
            if getattr(standards, toggle.key) != (toggle.key in chosen):
                standards.toggle(toggle.key)


# ---------------------------------------------------------------------- #
# 4. colours
# ---------------------------------------------------------------------- #


class ColorsScreen(StepScreen):
    """Walk the course's own colours; fix the ones that fail contrast.

    There is deliberately no palette to pick from. A fixed "conforming" palette
    is what produced #0645AD for a course blue of #3399E6 -- 8.53:1 where 4.5:1
    was asked for, and reported as harder to read than the colour it replaced.
    So: show what the course defines, flag what fails, propose the smallest
    change that clears the floor, and let the user confirm it, type their own,
    or keep the original.
    """

    heading = "Course colours"

    BINDINGS = [
        Binding("u", "use", "Use proposed"),
        Binding("k", "keep", "Keep original"),
        # `e`, not Enter. Enter is Next on every other step, and a screen where
        # it silently means "edit this row" instead is a screen you get stuck
        # on: you press the key that has moved you forward six times and the
        # cursor drops into a text field.
        Binding("e", "edit", "Edit hex"),
    ]

    @property
    def hint(self) -> str:  # type: ignore[override]
        colors = self.profile.colors
        return (
            f"Floor is {colors.min_contrast_normal}:1 on {colors.background} "
            "(WCAG 1.4.3 AA). * marks a colour you set by hand. "
            "↑ ↓ to a row, then u, k, or e to type your own hex."
        )

    def body(self) -> Iterator[Widget]:
        yield DataTable(id="colors", cursor_type="row")
        yield Static("", id="color-note", classes="note")
        with Horizontal(classes="row"):
            yield Static("Hex    enter", classes="gutter")
            yield Input(placeholder="#RRGGBB", id="hex", compact=True)
            yield Static("", id="hex-preview", classes="count")
        yield Static("", id="color-msg", classes="note")

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("use", "keep"):
            name = self.current
            usable = name is not None and proposal_for(self.profile, name) is not None
            return True if usable else None
        if action == "edit":
            # Any colour can be typed over, conforming or not -- unlike u and
            # k, which only mean something where there is a proposal.
            return True if self.current is not None else None
        return super().check_action(action, parameters)

    def action_back(self) -> None:
        """Escape cancels an edit before it means Back."""
        field = self.query_one("#hex", Input)
        if field.has_focus:
            field.value = ""
            self.query_one("#colors", DataTable).focus()
            return
        super().action_back()

    def on_mount(self) -> None:
        table = self.query_one("#colors", DataTable)
        table.add_columns(*COLOR_COLUMNS)
        self.reload()
        if not color_names(self.profile, self.config):
            self.query_one("#color-note", Static).update(
                "This profile names no colours, so there is nothing to measure. "
                "Whatever the document defines is still remapped to the "
                "conforming defaults."
            )

    def reload(self) -> None:
        table = self.query_one("#colors", DataTable)
        row = table.cursor_row
        table.clear()
        for cells in color_rows(self.profile, self.config):
            table.add_row(*cells)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))
        self._describe()

    @property
    def current(self) -> str | None:
        names = color_names(self.profile, self.config)
        index = self.query_one("#colors", DataTable).cursor_row
        return names[index] if 0 <= index < len(names) else None

    @on(DataTable.RowHighlighted, "#colors")
    def _row_highlighted(self) -> None:
        self._describe()

    def action_edit(self) -> None:
        """Type a new hex for the colour under the cursor.

        Reached with `e`. It used to be Enter, which collided with Enter
        meaning Next everywhere else.
        """
        name = self.current
        if name is None:
            return
        field = self.query_one("#hex", Input)
        field.value = self.config.colors.replacements(self.profile).get(
            name, self.profile.colors.originals.get(name, "")
        )
        field.focus()

    @on(Input.Changed, "#hex")
    def _hex_changed(self, event: Input.Changed) -> None:
        """Show the ink and the ratio of what is being typed, as it is typed."""
        try:
            value = normalise_hex(event.value)
        except LatexAllyError:
            self.query_one("#hex-preview", Static).update("")
            return
        self.query_one("#hex-preview", Static).update(
            Content.from_markup(
                f"{swatch(value)} {value} {contrast(self.profile, value)[1]}"
            )
        )

    def _mark_cursor(self) -> None:
        """Reverse the colour's *name*, not the whole row.

        A cursor drawn across the row covers the swatches, which are the cells
        on this screen that have to be seen rather than read. Reversing the one
        cell that is pure text says the same thing and hides nothing.
        """
        table = self.query_one("#colors", DataTable)
        column = next(iter(table.columns), None)
        if column is None:
            return
        rows = color_rows(self.profile, self.config)
        for index, (row, cells) in enumerate(zip(table.rows, rows)):
            name = cells[0].copy()
            if index == table.cursor_row:
                name.stylize("reverse")
            table.update_cell(row, column, name)

    def _describe(self) -> None:
        self._mark_cursor()
        name = self.current
        note = self.query_one("#color-note", Static)
        self.refresh_bindings()
        if name is None:
            note.update("")
            return
        original = self.profile.colors.originals.get(name, "")
        ratio, _ = contrast(self.profile, original)
        floor = floor_for(self.profile, name)
        proposed = proposal_for(self.profile, name)
        measured = f"{ratio:.2f}:1" if ratio is not None else "?"
        if proposed is None:
            note.update(
                Content(
                    f"{name} is {original} at {measured} — it already meets the "
                    f"{floor}:1 floor. Nothing will be changed."
                )
            )
            return
        note.update(
            Content.from_markup(
                f"{name} is {original} {swatch(original)} at {measured} — below "
                f"the {floor}:1 floor.\n"
                f"  Smallest change that reaches it: [bold]{proposed}[/] "
                f"{swatch(proposed)} {contrast(self.profile, proposed)[1]}  "
                "[dim](same hue and saturation, darkened only as far as "
                "needed)[/]"
            )
        )

    def action_use(self) -> None:
        name = self.current
        if name is None:
            return
        original = self.profile.colors.originals.get(name, "")
        self.config.colors.reset(name)
        self._message(f"{name}: {original} → {proposal_for(self.profile, name)}")

    def action_keep(self) -> None:
        name = self.current
        if name is None:
            return
        original = self.profile.colors.originals.get(name, "")
        ratio = contrast(self.profile, original)[0]
        # Recorded as an override to itself, so "the user decided to keep this"
        # and "nobody has looked at this yet" stay distinguishable.
        self.config.colors.set(name, original)
        measured = f"{ratio:.2f}:1" if ratio is not None else "?"
        self._message(
            f"Kept at {measured}. `latexally check` will report it, and the "
            "build will not conform on this point."
        )

    @on(Input.Submitted, "#hex")
    def _set(self) -> None:
        name = self.current
        if name is None:
            return
        value = self.query_one("#hex", Input).value.strip()
        if not value:
            return
        try:
            normalised = normalise_hex(value)
        except LatexAllyError as exc:
            self._message(str(exc))
            return
        self.config.colors.set(name, normalised)
        self.query_one("#hex", Input).value = ""
        self.query_one("#hex-preview", Static).update("")
        # Back to the table: while the field holds focus every letter key is
        # text, so u and k would type rather than act.
        self.query_one("#colors", DataTable).focus()
        ratio = contrast(self.profile, normalised)[0]
        floor = floor_for(self.profile, name)
        message = f"{name} → {normalised}"
        if ratio is not None and ratio < floor:
            # Accepted, but never silently: the whole point of this step is that
            # what comes out of it conforms, and a chosen value that does not
            # would otherwise look identical to one that does.
            message += (
                f". That is below the {floor}:1 floor. It will be used as "
                "given; `latexally check` will report it."
            )
        self._message(message)

    def _message(self, text: str) -> None:
        self.say("#color-msg", text)
        self.reload()


# ---------------------------------------------------------------------- #
# 5. alt text
# ---------------------------------------------------------------------- #

ALT_TEMPLATE_WARNING = (
    "Every figure without a description gets <<TODO:figure-id>> written into "
    "the .tex where an author will see it, and is listed in the alt-text "
    "worklog.\n\n"
    "latexally-core refuses <<TODO:…>> as alt text, so the document FAILS TO "
    "BUILD until each one is filled in. That refusal is the point: the previous "
    "generation of this tooling shipped unfilled markers into PDFs as real "
    "/Alt, passing both a naive \"every Figure has /Alt\" check and veraPDF — "
    "a silent false claim of conformance."
)


class AltScreen(StepScreen):
    """Figure descriptions: write the template, or leave figures alone."""

    heading = "Write an alt-text template for undescribed figures?"

    def body(self) -> Iterator[Widget]:
        # `scans`, not `injects`: the shipped default is the old "worklog" tier,
        # which no longer has a screen of its own but does mean "look at
        # figures", so it opens here as on rather than silently as off.
        alt = self.config.alt
        with Horizontal(classes="row"):
            yield Static("Alt    ↑ ↓", classes="gutter")
            yield Choice(
                Radio(
                    "on — mark every undescribed figure, and refuse to build "
                    "until each one is filled in",
                    value=alt.scans and alt.strict,
                    name="on",
                ),
                Radio(
                    "draft — mark them, report them, build anyway. The PDF is "
                    "NOT conformant",
                    value=alt.scans and not alt.strict,
                    name="draft",
                ),
                Radio(
                    "off — skip figures entirely",
                    value=not alt.scans,
                    name="off",
                ),
                id="alt-mode",
            )
        yield Static("", id="alt-draft", classes="note")
        yield Static(ALT_TEMPLATE_WARNING, id="alt-warning", classes="detail")

    def on_mount(self) -> None:
        self._sync()

    @on(RadioSet.Changed)
    def _changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        pressed = self.query_one("#alt-mode", RadioSet).pressed_button
        mode = pressed.name if pressed else ("on" if self.config.alt.scans else "off")
        scans = mode in ("on", "draft")
        self.query_one("#alt-warning").display = scans
        # `draft` is offered, and says what it costs on the control itself.
        # It was withheld on the grounds that turning strict off is what lets a
        # placeholder reach a reader as if it were a description -- true, and
        # the answer to it is that the cost is stated rather than the choice
        # removed. What draft must never be is quiet: every figure that says
        # nothing is still named in the report, the build is still marked, and
        # `latexally check` still fails on the artefact.
        self.say(
            "#alt-draft",
            ""
            if mode != "draft"
            else (
                "Draft builds a PDF with figures that say nothing — their /Alt "
                "is a placeholder or a file name. It passes a naive 'every "
                "Figure has /Alt' check and veraPDF, so it can be mistaken for "
                "a conforming document. For looking at a page, never for "
                "handing out."
            ),
        )
        self.config.alt = AltChoice(
            mode="placeholders" if scans else "off", strict=mode != "draft"
        )


# ---------------------------------------------------------------------- #
# 6. output
# ---------------------------------------------------------------------- #


class OutputScreen(StepScreen):
    """Where everything goes, and whether the corpus is edited at all."""

    heading = "Where does the output go?"
    hint = (
        "A blank artifact path restores its default; a relative one hangs off "
        "the root."
    )

    def container(self) -> Widget:
        return VerticalScroll(id="body")

    #: The consequential choice on this screen, so it is where the cursor
    #: starts -- otherwise focus landed on the scrolling container and the
    #: arrows scrolled the screen instead of choosing.
    AUTO_FOCUS = "#write-mode"

    def body(self) -> Iterator[Widget]:
        output = self.config.output
        with Horizontal(classes="row"):
            yield Static("Write  ↑ ↓", classes="gutter")
            yield Choice(
                Radio(
                    "mirror — write converted .tex + PDFs to the output tree; "
                    "corpus read-only",
                    value=output.write_mode == "mirror",
                    name="mirror",
                ),
                Radio(
                    "in-place — the PDF goes beside the original, in the "
                    "corpus; no .tex is edited",
                    value=output.write_mode == "in-place",
                    name="in-place",
                ),
                Radio(
                    "edit — ALSO rewrites the corpus .tex, so the folder "
                    "builds with a bare pdflatex. Undo with r",
                    value=output.edits_sources,
                    name="edit",
                ),
                id="write-mode",
            )
        # Said once, below both modes it applies to. It used to live inside the
        # in-place label, which had no room for it and no way to also cover
        # edit -- the mode where it matters far more.
        yield Static(
            "in-place and edit both need a clean git worktree. That is what "
            "makes them undoable.",
            classes="hint",
        )
        with Horizontal(classes="row"):
            yield Static("Root   type", classes="gutter")
            yield Input(value=str(output.root), id="root", compact=True)
        yield Static(
            "Individual artifact locations — tab to reach them", classes="label"
        )
        for slug, label, note in ARTIFACTS:
            yield Static(f"{label} — {note}", classes="hint")
            yield Input(
                value=str(output.paths.get(slug, "")),
                placeholder=under(output.path_for(slug), output.root),
                id=f"path-{slug}",
                name=slug,
                compact=True,
            )
        yield Static("", id="output-note", classes="note")

    def on_mount(self) -> None:
        self._sync()

    @on(Input.Changed)
    @on(RadioSet.Changed)
    def _changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        output = self.config.output
        root = self.query_one("#root", Input).value.strip()
        if root:
            output.root = Path(root).expanduser()
        pressed = self.query_one("#write-mode", RadioSet).pressed_button
        if pressed and pressed.name in WRITE_MODES:
            output.write_mode = pressed.name
        problems: list[str] = []
        for slug, label, _ in ARTIFACTS:
            field = self.query_one(f"#path-{slug}", Input)
            try:
                output.set_path(slug, field.value.strip() or None)
            except LatexAllyError as exc:
                problems.append(f"{label}: {exc}")
            field.placeholder = under(output.path_for(slug), output.root)
        self.say("#output-note", "\n".join(problems))
        self.set_next(not problems, "Fix the paths above.")


# ---------------------------------------------------------------------- #
# review
# ---------------------------------------------------------------------- #


class ReviewScreen(StepScreen):
    """Everything this run will do, before anything is written.

    The last chance to see the exact preamble and every file touched. Nothing
    has been written up to this point, and the button below says which of the
    two very different things is about to happen.
    """

    heading = "Review"
    #: Enter writes from here, so the footer must not keep calling it "Next".
    BINDINGS = [
        Binding("enter", "advance", "Build", priority=True),
        Binding("n", "next", "Build", show=False),
    ]

    def container(self) -> Widget:
        return VerticalScroll(id="body")

    @staticmethod
    def section(title: str) -> Iterator[Widget]:
        """A ruled heading. This screen is six unrelated answers in a column.

        Bold text alone did not separate them: the preamble is thirty lines of
        LaTeX and the document table has its own header row, so "Colours" read
        as one more line of whatever was above it. A rule is the cheapest thing
        that says "different question".
        """
        yield Rule(classes="divider")
        yield Static(title, classes="section")

    def body(self) -> Iterator[Widget]:
        yield Static("", id="verdict", classes="heading")
        yield Static("", id="settings", classes="note")

        yield from self.section("Selected directories")
        yield Static("", id="scope-list", classes="note")

        yield from self.section("What gets built")
        yield Static("", id="documents")
        yield Static(
            "'.tex used' counts every file the driver reaches, including "
            "questions \\input from the shared bank — which is where most "
            "figures live.",
            classes="hint",
        )

        yield from self.section("Figure descriptions")
        yield Static("", id="alt", classes="note")
        yield Static("", id="alt-markup", classes="detail")

        yield from self.section("Colours")
        yield Static("", id="colors")

        yield from self.section("Output")
        yield Static("", id="output")

        # Last, and deliberately: thirty lines of LaTeX that almost nobody
        # reads twice. Above the document table it pushed the one thing people
        # do check -- which documents will be built -- below the fold.
        yield from self.section("Injected into each driver")
        yield Static("", id="preamble", classes="detail")

    def on_mount(self) -> None:
        from ..build import preamble_for

        config, profile = self.config, self.profile
        count = len(config.assignments)
        # Three modes, three sentences. The one that rewrites course material
        # must not read like the one that only drops a PDF next to it, and the
        # last screen before anything is written is where that has to be said.
        if config.output.edits_sources:
            verdict = (
                f"Build {count} assignment(s), write each PDF into the corpus, "
                "AND REWRITE THE .tex FILES THEMSELVES?\n"
                "Your course sources will be modified. Press r at any time to "
                "undo it."
            )
        elif config.output.in_place:
            verdict = (
                f"Build {count} assignment(s) and write each PDF into the corpus, "
                "beside the document it came from? (no .tex is edited)"
            )
        else:
            verdict = (
                f"Build {count} assignment(s) into {config.output.root}? "
                "(your corpus is not modified)"
            )
        self.query_one("#verdict", Static).update(Content(verdict))
        self.query_one("#settings", Static).update(
            Content(
                "\n".join(
                    (
                        f"Documents: {describe_variants(config)}",
                        f"Standards: {len(config.standards.enabled())} of "
                        f"{len(STANDARD_TOGGLES)} applied",
                        f"Colours:   {config.colors.describe(profile)}",
                        # Alt text is not repeated here: it has a section of
                        # its own below, which says the same thing and then
                        # shows the markup it puts in the file.
                        f"Output:    {describe_output(config)}",
                    )
                )
            )
        )
        try:
            lines = preamble_for(config, profile)
        except LatexAllyError as exc:
            lines = [str(exc)]
        self.query_one("#preamble", Static).update(
            Content("\n".join(lines) or "nothing — every standard is off")
        )
        self.query_one("#scope-list", Static).update(
            Content("\n".join(config.assignments) or "nothing selected")
        )
        self._alt_text(config)
        self.query_one("#documents", Static).update(self._documents())
        self.query_one("#colors", Static).update(colors_table(profile, config))
        self.query_one("#output", Static).update(output_table(config))
        self.set_next(bool(count), "Nothing is selected; go back to Scope.")

    def _alt_text(self, config) -> None:
        r"""Say what reaches the .tex, in the characters that reach it.

        The alt-text step warns that placeholders are written; it does not show
        what they look like. This is the last screen before they are, and a
        marker in somebody's course file is exactly the kind of thing that
        should not be a surprise -- the previous generation of this tooling
        shipped one into a PDF as real /Alt.
        """
        from ..apply import PLACEHOLDER

        self.say("#alt", config.alt.describe())
        if not config.alt.injects:
            self.say("#alt-markup", "")
            return
        marker = PLACEHOLDER.format(id="fig-1a2b3c4d")
        self.say(
            "#alt-markup",
            "Each undescribed figure is wrapped where it stands:\n"
            f"  \\begin{{Described}}{{{marker}}}\n"
            "    …the figure, byte for byte as you wrote it…\n"
            "  \\end{Described}\n"
            f"  \\described{{{marker}}}{{…}}   (for a graphic sharing its line)\n"
            "\n"
            "A figure that already has a description keeps it and is not "
            "touched. The marker is refused as alt text, so the document does "
            "NOT build until every one is filled in — which is what stops an "
            "unfilled marker reaching a reader as if it were a description.",
        )

    def _documents(self):
        from rich.table import Table

        from ..build import source_files_for
        from ..discover import iter_selected

        table = Table(box=None, header_style="bold")
        for column in ("assignment", "document", "driver"):
            table.add_column(column)
        table.add_column(".tex used", justify="right")
        table.add_column("→ PDF", overflow="fold")
        try:
            selected = list(iter_selected(self.profile, self.config))
        except LatexAllyError as exc:
            return Text(str(exc), style="red")
        for assignment in selected:
            used = str(len(source_files_for(assignment, self.profile)))
            variants = assignment.variants_for(self.config.variants)
            if not variants:
                table.add_row(assignment.path, Text("none", style="red"), "", used, "")
                continue
            for variant, driver in variants.items():
                table.add_row(
                    assignment.path,
                    variant,
                    driver,
                    used,
                    show_path(self.config.output.pdf_dir() / f"{_slug(assignment.path, variant)}.pdf"),
                )
        return table


def _number(value: int | None) -> str:
    return "—" if value is None else str(value)


def _slug(path: str, variant: str) -> str:
    """The jobname ``build`` will use -- from ``build`` itself, not a copy.

    This was a duplicate of ``base_slug``, and a duplicate of a naming rule is a
    preview that quietly stops matching what gets written.
    """
    from ..build import accessible_slug, base_slug

    return accessible_slug(base_slug(path, variant))


# ---------------------------------------------------------------------- #
# build
# ---------------------------------------------------------------------- #


class BuildScreen(Screen):
    """The run itself, with a row per document and the live LaTeX log.

    ``build_run`` is blocking, so it runs in a thread and posts back through
    ``call_from_thread``. Its ``on_start``/``on_finish`` hooks already exist for
    exactly this -- the engine has never had to know what a terminal is.
    """

    BINDINGS = [Binding("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Static("Building…", id="build-status", classes="heading")
        yield DataTable(id="progress")
        yield Static("Latest log", classes="label")
        yield RichLog(id="log", max_lines=2000)
        yield Static("", id="build-hint", classes="note")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#progress", DataTable)
        table.add_column("assignment")
        table.add_column("document")
        # Explicit, because a DataTable sizes a column to whatever was in it
        # when the rows were added -- here "queued", which then crops
        # "building…" and "FAILED" down to six characters.
        table.add_column("state", width=10)
        for name in ("pages", "bookmarks", "figures"):
            table.add_column(name, width=9)
        table.add_column("pixel diff", width=11)
        for key, assignment, variant in self._queue():
            table.add_row(assignment, variant, "queued", "", "", "", "", key=key)
        self._log_path: Path | None = None
        self._log_at = 0
        self.set_interval(0.4, self._tail)
        self.build()

    def _queue(self) -> list[tuple[str, str, str]]:
        from ..discover import iter_selected

        rows: list[tuple[str, str, str]] = []
        try:
            selected = list(iter_selected(self.app.profile, self.app.config))
        except LatexAllyError:
            return rows
        for assignment in selected:
            for variant in assignment.variants_for(self.app.config.variants) or {
                "document": None
            }:
                rows.append((f"{assignment.path}|{variant}", assignment.path, variant))
        return rows

    # -- the worker ----------------------------------------------------- #

    @work(thread=True, exclusive=True)
    def build(self) -> None:
        from ..build import build_run, describe_run

        config, profile = self.app.config, self.app.profile
        try:
            descriptions = describe_run(config, profile)
        except LatexAllyError:
            descriptions = {}
        try:
            reports = build_run(
                config,
                profile,
                on_start=lambda item, variant: self.app.call_from_thread(
                    self._started, item, variant
                ),
                on_finish=lambda report: self.app.call_from_thread(
                    self._finished, report
                ),
            )
        except LatexAllyError as exc:
            self.app.call_from_thread(self._failed, str(exc))
            return
        self.app.call_from_thread(self._done, reports, descriptions)

    def _row(self, key: str, *cells: str) -> None:
        table = self.query_one("#progress", DataTable)
        try:
            for column, value in zip(table.columns, cells):
                if value is not None:
                    table.update_cell(key, column, value)
        except CellDoesNotExist:
            # A document the queue did not predict. Narrow on purpose: a bare
            # `except` here would swallow any bug in the progress table during
            # a build, which is exactly when nobody is watching the code.
            pass

    def _started(self, assignment, variant: str) -> None:
        key = f"{assignment.path}|{variant}"
        self._row(key, None, None, "building…")
        jobs = self.app.config.jobs
        if jobs > 1:
            # With N documents in flight there is no "the" log to follow, and
            # following whichever one started last would interleave three
            # unrelated pdflatex runs into one pane. The per-row status column
            # still tracks each document, and `run.log` carries every log,
            # banner-separated, after the run.
            self._log_path = None
            self.query_one("#build-status", Static).update(
                Content(f"Building, {jobs} documents at a time…")
            )
            return
        slug = _slug(assignment.path, variant)
        output = self.app.config.output
        self._log_path = output.pdf_dir() / f"{slug}.log"
        self._log_at = 0
        self.query_one("#build-status", Static).update(
            Content(f"Building {assignment.path} ({variant})…")
        )

    def _finished(self, report) -> None:
        key = f"{report.assignment}|{report.variant}"
        self._row(
            key,
            None,
            None,
            "done" if report.ok else ("errors" if report.built else "FAILED"),
            _number(report.pages),
            _number(report.bookmarks),
            _number(report.figures),
            # A fraction on the report, a percentage on the screen -- and the
            # reason it could not be measured when there is no number at all.
            f"{100 * report.pixel_diff:.2f}%"
            if report.pixel_diff is not None
            else (report.diff_note or "—"),
        )

    def _tail(self) -> None:
        """Follow the LaTeX log of whatever is compiling right now.

        No engine change: pdflatex already writes it there. The file is moved
        into its own directory only after the run, so the live one is the copy
        next to the PDF.
        """
        path = self._log_path
        if path is None or not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._log_at)
                fresh = handle.read()
                self._log_at = handle.tell()
        except OSError:
            return
        if fresh:
            self.query_one("#log", RichLog).write(fresh.rstrip("\n"))

    # -- completion ----------------------------------------------------- #

    def _failed(self, message: str) -> None:
        self.app.reports = []
        self.query_one("#build-status", Static).update(Content(f"Failed: {message}"))
        self._offer_exit()

    def _done(self, reports, descriptions) -> None:
        from ..cli import _report_table

        self.app.reports = reports
        self.app.descriptions = descriptions
        failures = [report for report in reports if not report.ok]
        # Three outcomes, said as three: a clean build, a PDF with something in
        # its log worth reading, and nothing at all. The middle one used to be
        # drawn as the last one, which sent people hunting for output that was
        # already on disk.
        flagged = [report for report in reports if report.built and not report.ok]
        missing = [report for report in reports if not report.built]
        parts = [f"{len(reports) - len(failures)} of {len(reports)} built clean"]
        if flagged:
            parts.append(f"{len(flagged)} with errors in the log")
        if missing:
            parts.append(f"{len(missing)} produced nothing")
        self.query_one("#build-status", Static).update(Content(", ".join(parts)))
        log = self.query_one("#log", RichLog)
        log.write(_report_table(reports))
        if descriptions.get("scanned") and descriptions.get("outstanding"):
            log.write(
                Text(
                    f"{descriptions['outstanding']} figure(s) still need alt text:",
                    style="bold",
                )
            )
            for path in descriptions.get("worklogs", [])[:8]:
                log.write(Text(f"  {path}"))
        for report in failures:
            log.write(
                Text(
                    f"{report.assignment} ({report.variant}) "
                    + (
                        f"built, with {len(report.errors)} error(s) in the log"
                        if report.built
                        else "failed — no PDF"
                    ),
                    style="yellow" if report.built else "red",
                )
            )
            if report.note:
                log.write(Text(f"  {report.note}"))
            for line in report.errors[:5]:
                log.write(Text(f"  {line}", style="red"))
            if len(report.errors) > 5:
                log.write(Text(f"  …{len(report.errors) - 5} more", style="dim"))
            if report.log:
                from ..build import _slug_for

                log.write(
                    Text(
                        f"  full log: {show_path(report.log)}"
                        f"  (search '=== {_slug_for(report)}')",
                        style="dim",
                    )
                )
        if failures:
            log.write(
                Text(
                    "`latexally doctor --tagging <scope>` locates constructs that "
                    "tagging cannot compile, without rebuilding, and `--fix` "
                    "rewrites the ones that can be rewritten.",
                    style="dim",
                )
            )
        self._offer_exit()

    def _offer_exit(self) -> None:
        saved = self.app.config.output.root / "build-log.txt"
        self.query_one("#build-hint", Static).update(
            Content(
                f"written to {show_path(saved)}    q to close"
                if saved.is_file()
                else "q to close"
            )
        )


# ---------------------------------------------------------------------- #
# revert
# ---------------------------------------------------------------------- #


class RevertScreen(Screen):
    """Undo a run, after showing exactly what that means.

    Reachable with ``r`` from anywhere, and deliberately not a step: reverting
    is not part of converting, and putting it in the wizard would make it
    something you walk past on the way to a build.

    It opens on the plan, never on the act. The three groups are listed by name
    and by count, and ``y`` is the only key that writes -- because the thing
    being undone is somebody's course material and "I pressed enter out of
    habit" must not be able to reach it.
    """

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("y", "confirm", "Revert"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Revert", classes="heading")
        yield Static(
            "Puts your .tex back with git and deletes what this tool wrote. "
            "Files it does not recognise are left alone.",
            classes="hint",
        )
        with VerticalScroll(id="body"):
            yield Static("", id="revert-plan")
        yield Static("", id="revert-note", classes="reason")
        yield Footer()

    def on_mount(self) -> None:
        self._plan = None
        self._load()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "confirm":
            # Greyed rather than dropped, so the footer still answers "why can
            # I not press y" while the plan is loading or empty.
            return True if self._plan is not None and not self._plan.empty else None
        return True

    def _load(self) -> None:
        from ..revert import plan_revert

        try:
            self._plan = plan_revert(self.app.config, self.app.profile)
        except LatexAllyError as exc:
            self.query_one("#revert-plan", Static).update(Content(str(exc)))
            self.refresh_bindings()
            return
        self.query_one("#revert-plan", Static).update(self._describe(self._plan))
        self.refresh_bindings()

    def _describe(self, plan) -> Content:
        if plan.empty:
            return Content("Nothing to revert — the corpus is clean and there "
                           "is no output tree.")
        lines: list[str] = []
        root = plan.root

        def group(title: str, paths: list, relative: bool) -> None:
            if not paths:
                return
            lines.append(f"{title} ({len(paths)})")
            for path in paths[:12]:
                try:
                    shown = path.relative_to(root) if relative else path
                except ValueError:
                    shown = path
                lines.append(f"  {shown if relative else show_path(path)}")
            if len(paths) > 12:
                lines.append(f"  …{len(paths) - 12} more")
            lines.append("")

        group("Restored with git", plan.restore, True)
        group("Deleted", plan.remove, True)
        group("Output removed", plan.outputs, False)
        return Content("\n".join(lines).rstrip())

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_confirm(self) -> None:
        from ..revert import do_revert

        if self._plan is None or self._plan.empty:
            return
        try:
            do_revert(self._plan)
        except LatexAllyError as exc:
            self.say(str(exc))
            return
        self.say(
            f"Reverted: {len(self._plan.restore)} restored, "
            f"{len(self._plan.remove)} deleted. esc to go back."
        )
        self._load()

    def say(self, text: str) -> None:
        note = self.query_one("#revert-note", Static)
        note.update(Content(text))
        note.display = bool(text)


# ---------------------------------------------------------------------- #
# the app
# ---------------------------------------------------------------------- #

STEPS: tuple[type[StepScreen], ...] = (
    ModeScreen,
    ScopeScreen,
    DocumentsScreen,
    StandardsScreen,
    ColorsScreen,
    AltScreen,
    OutputScreen,
    ReviewScreen,
)


class LatexAllyApp(App):
    """The runner. One :class:`RunConfig`, one screen per decision."""

    TITLE = "latexally run"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "save", "Save run.yaml"),
        Binding("r", "revert", "Revert"),
    ]
    CSS = """
    Screen { background: transparent; }
    .heading { text-style: bold; padding: 0 1; }
    .hint { color: $text-muted; padding: 0 1; }
    .label { text-style: bold; padding: 0 1; }
    /* Section headings on Review. The rule does the separating; the heading
       is indented to sit with the block it names rather than with the rule. */
    .section { text-style: bold; padding: 0 1; }
    .divider { color: $text-muted; margin: 1 1 0 1; }
    .note { padding: 0 1; }
    .count { padding: 0 1; color: $text-muted; }
    .row { height: auto; }
    /* The key that moves a row is printed in that row's own gutter, so the
       affordance sits beside the thing it acts on rather than in a footer. */
    .gutter { width: 14; padding: 0 1; color: $text-muted; }
    /* 1fr so a long scope row wraps instead of clipping a scope off the end. */
    .rowtext { width: 1fr; }
    .detail { color: $text-muted; padding: 0 1; height: auto; max-height: 12; }
    .reason { color: $text-muted; height: 1; padding: 0 1; }
    #body { height: 1fr; padding: 0 1; }
    SelectionList { height: 1fr; background: transparent; border: none; }
    DataTable { height: 1fr; background: transparent; }
    /* Reverse video, not a coloured bar: it is legible against whatever the
       terminal's own palette is, and on the colour table it is confined to the
       name cell so the swatches -- the cells that must be seen rather than
       read -- stay uncovered. */
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted,
    OptionList:blur > .option-list--option-highlighted,
    OptionList > .option-list--option-hover,
    DataTable > .datatable--cursor,
    DataTable > .datatable--hover { background: transparent; }
    RadioSet { background: transparent; border: none; padding: 0; }
    RadioSet > Radio { background: transparent; height: auto; }
    RadioSet:focus > Radio.-selected > .toggle--label,
    RadioSet:blur > Radio.-selected > .toggle--label {
        background: transparent; text-style: bold;
    }
    RichLog { height: 1fr; background: transparent; border: none; }
    LoadingIndicator { height: 1; background: transparent; }
    Input { border: none; background: transparent; padding: 0 1; }
    """

    def __init__(self, profile: Profile, config: RunConfig | None = None) -> None:
        super().__init__()
        # ANSI only, so the app inherits the terminal's own palette and
        # background instead of painting a dark window over it.
        self.theme = "ansi-light"
        self.profile = profile
        self.config = config or RunConfig(profile=profile.name)
        # Same anchoring the CLI does, so the runner and the flags agree about
        # where a defaulted output root points.
        self.config.output.anchor(profile)
        #: The corpus-relative directory the runner was started from, or None
        #: when that is outside the corpus. Same answer `latexally scan` uses,
        #: from the same function, so the runner and the commands cannot
        #: disagree about what "here" means.
        self.here_scope = scope_from_cwd(profile)
        #: "local" -- convert what `here_scope` names -- or "choose". Answered
        #: on the first step, and it decides whether the scope picker is part
        #: of this run at all.
        self.scope_mode = "local" if self.here_scope is not None else "choose"
        self.should_run = False
        self.reports: list = []
        self.descriptions: dict = {}
        self._index = 0

    def on_mount(self) -> None:
        self.push_screen(STEPS[0]())

    #: Every run walks all of them. `local` mode does not skip the scope step:
    #: it pre-answers it, and the screen still has to show what that answer
    #: covers so any of it can be unticked.
    active_steps = STEPS

    def step(self, delta: int) -> None:
        """Move one step. Back pops, so the screen behind keeps its state."""
        steps = self.active_steps
        index = self._index + delta
        if index < 0:
            return
        if index >= len(steps):
            self.start_build()
            return
        self._index = index
        if delta < 0:
            self.pop_screen()
        else:
            self.push_screen(steps[index]())

    def start_build(self) -> None:
        self.config = replace(self.config, write=True)
        self.should_run = True
        self.push_screen(BuildScreen())

    def action_revert(self) -> None:
        """Undo a run. Available from every step, including after the build.

        Not a wizard step: it is the opposite of one. Pushed rather than
        switched to, so escape puts the user back exactly where they were.
        """
        if isinstance(self.screen, RevertScreen):
            return
        self.push_screen(RevertScreen())

    def action_save(self, path: Path | None = None) -> Path:
        path = path or (self.config.output.root / "run.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.config.to_yaml(), encoding="utf-8")
        self.notify(
            f"saved {show_path(path)} — replay with "
            f"latexally run --config {show_path(path)}"
        )
        return path
