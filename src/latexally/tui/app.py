"""The interactive runner, as a Textual app."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterator

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on, work
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

from ..config import (
    Profile,
    builtin_profile_names,
    default_builtin_profile,
    load_profile,
    profile_summary,
)
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
    """A selection list that says what it means in characters, not in shades."""

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
        return strip.apply_style(Style(reverse=True)) if highlighted else strip


class Choice(RadioSet):
    """A radio group where ``↑`` ``↓`` choose rather than shop around."""

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


class StepScreen(Screen):
    """One step: a heading, a body, and a footer that always names the keys."""

    heading = ""
    hint = ""

    BINDINGS = [
        Binding("backslash", "back", "Back", key_display="\\"),
        Binding("b", "back", "Back", show=False),
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
        detail = Static("", id="detail-box", classes="detail-box")
        detail.display = False
        yield detail
        yield Footer()


    @property
    def first(self) -> bool:
        return self.app.active_steps.index(type(self)) == 0

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "back":
            return False if self.first else True
        if action == "next":
            return True if self._can_next else None
        if action == "advance":
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
        """Update a note, and give its row back when there is nothing to say."""
        widget = self.query_one(selector, Static)
        widget.update(Content(text))
        widget.display = bool(text)

    def describe(self, title: str, text: str = "") -> None:
        """Explain the selected option, in the box every step keeps at the bottom."""
        try:
            box = self.query_one("#detail-box", Static)
        except NoMatches:  # pragma: no cover - called before compose
            return
        box.border_title = title
        box.update(Content(text))
        box.display = bool(text)

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


class ProfileScreen(StepScreen):
    """Which course, when more than one profile is installed."""

    heading = "Which course?"
    hint = "Every later step reads this one's corpus."
    AUTO_FOCUS = "#profile-choice"

    def body(self) -> Iterator[Widget]:
        names = self.app.profile_names
        current = self.app.profile.name
        with Horizontal(classes="row"):
            yield Static("Course  ↑ ↓", classes="gutter")
            yield Choice(
                *(
                    Radio(
                        profile_summary(name),
                        value=name == current,
                        name=name,
                    )
                    for name in names
                ),
                id="profile-choice",
            )
        yield Static("", id="profile-note", classes="note")

    def on_mount(self) -> None:
        self._sync()

    @on(RadioSet.Changed, "#profile-choice")
    def _changed(self) -> None:
        pressed = self.query_one("#profile-choice", RadioSet).pressed_button
        if pressed is None or pressed.name is None:
            return
        try:
            self.app.use_profile(pressed.name)
        except LatexAllyError as exc:
            self.set_next(False, str(exc))
            return
        self.set_next(True)
        self._sync()

    def _sync(self) -> None:
        here = self.app.here_scope
        root = self.app.profile.corpus.root
        self.say(
            "#profile-note",
            f"corpus {root}"
            + (f"   ·   you are in {here or 'the corpus root'}" if here is not None
               else "   ·   you are outside this corpus"),
        )


class ModeScreen(StepScreen):
    """Local, or pick from the corpus. Asked before anything is listed."""

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


class ScopeScreen(ListStepScreen):
    """The one question the tool cannot answer for you, asked first."""

    heading = "What do you want to convert?"
    hint = (
        "Starts on the folder you ran this from. Next: documents, standards, "
        "colours, output."
    )
    list_id = "assignments"

    BINDINGS = [
        Binding("left", "scope(-1)", "Scope", priority=True),
        Binding("right", "scope(1)", "Scope", show=False, priority=True),
    ]

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Claim the arrows only where they mean "other scope" / "other kind"."""
        if action == "scope":
            typing = isinstance(self.app.focused, Input)
            if typing or not self._scopes:
                return False
            try:
                return True if self.query_one("#scope-line").display else False
            except NoMatches:
                return True
        return super().check_action(action, parameters)

    def __init__(self) -> None:
        super().__init__()
        self._scopes: list[str] = []
        self._scope_index: int | None = None
        self._scope = ""
        self._buildable: list[Assignment] = []
        self._chosen: set[str] = set()
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
        """Is this run scoped to the folder the runner was started from?"""
        return self.app.scope_mode == "local"

    def _apply_mode(self, browsing: bool | None = None) -> None:
        """Show the browsing controls only when they are of use."""
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
            self.query_one("#scope-line").display = bool(self._scopes)
            self.choices.focus()
        else:
            self._apply_mode()


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


    def scan(self, scope: str) -> None:
        """Start a scan. The answer arrives in :meth:`_scanned`."""
        self._scope = scope
        self._buildable = []
        self._showing = []
        self._rebuilding = True
        self.choices.clear_options()
        self._rebuilding = False
        self.query_one("#scanning").display = True
        self.choices.focus()
        self.say("#scope-note", f"scanning {scope or 'the corpus'}…")
        self.say("#pick-note", "")
        self._render_rows()
        self._sync()
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
            notes.append(
                f"{skipped} director{_plural(skipped)} skipped — no file "
                "containing \\begin{document}"
            )

        self._buildable = buildable
        if buildable:
            grouped = group_by_kind(buildable)
            notes.append(
                f"{len(buildable)} director{_plural(len(buildable))} — "
                + ", ".join(f"{len(items)} {kind}" for kind, items in grouped.items())
            )
        if not buildable and self._here:
            self._apply_mode(browsing=True)
        self.refresh_bindings()
        self.say("#scope-note", "\n".join(notes))
        self.refresh_list()


    def refresh_list(self) -> None:
        self.query_one("#scanning").display = False
        items = self._buildable
        only = len(items) == 1
        if only:
            self._chosen.add(items[0].path)
        if self._here:
            self._chosen.update(item.path for item in items)
        self._showing = [item.path for item in items]
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
        self.choices.highlighted = 0 if items else None
        self._rebuilding = False
        self.say(
            "#pick-note", f"{items[0].path} — the only assignment here" if only else ""
        )
        if items:
            self.choices.focus()
        self._sync()

    @on(SelectionList.SelectedChanged, "#assignments")
    def _selection_changed(self) -> None:
        if self._rebuilding:
            return
        self._chosen.difference_update(self._showing)
        self._chosen.update(self.choices.selected)
        self._sync()

    def _sync(self) -> None:
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
        shown = ", ".join(elsewhere[:3]) + (
            f" +{len(elsewhere) - 3} more" if len(elsewhere) > 3 else ""
        )
        self.say("#elsewhere", f"also ticked: {shown}" if elsewhere else "")
        self._render_rows()
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


class DocumentsScreen(ListStepScreen):
    """Which documents of each assignment to build."""

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
            self.config.variants = (
                () if set(chosen) == set(DEFAULT_VARIANTS) & set(VARIANTS) else chosen
            )
        self.say("#variants-note", describe_variants(self.config))
        self.set_next(
            bool(chosen),
            "That would build nothing. Leave at least one document ticked.",
        )


class StandardsScreen(ListStepScreen):
    """Which accessibility standards this run applies."""

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


class ColorsScreen(StepScreen):
    """Walk the course's own colours; fix the ones that fail contrast."""

    heading = "Course colours"

    BINDINGS = [
        Binding("k", "keep", "Reject (keep original)"),
        Binding("u", "use", "Undo reject"),
        Binding("e", "edit", "Edit hex"),
    ]

    @property
    def hint(self) -> str:  # type: ignore[override]
        colors = self.profile.colors
        if self.config.colors.mode == "palette":
            return (
                "Every colour below is already remapped — this screen is here to "
                f"reject one, not to approve them. Floor is "
                f"{colors.min_contrast_normal}:1 on {colors.background} "
                "(WCAG 1.4.3 AA). * marks a row you changed. "
                "↑ ↓ to a row, then k to keep the course original, u to undo "
                "that, e to type your own hex. The dim rows are what a picture "
                "draws in: a line sits beside black axes, so those are measured "
                "against the page AND the ink, 3:1 on both (WCAG 1.4.11)."
            )
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
        name = self.current
        if action == "keep":
            rejectable = (
                name is not None
                and self._proposal(name) is not None
                and not self._is_kept(name)
            )
            return True if rejectable else None
        if action == "use":
            return True if name is not None and name in self.config.colors.overrides else None
        if action == "edit":
            return True if name is not None else None
        return super().check_action(action, parameters)

    def _is_kept(self, name: str) -> bool:
        """Whether this colour has been rejected back to the course's own value."""
        original = self.profile.colors.originals.get(name) or ""
        chosen = self.config.colors.overrides.get(name)
        return bool(chosen) and chosen.upper() == original.upper()

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
        """Type a new hex for the colour under the cursor."""
        name = self.current
        if name is None:
            return
        field = self.query_one("#hex", Input)
        field.value = (
            self.config.colors.replacements(self.profile).get(name)
            or self._proposal(name)
            or self.profile.colors.originals.get(name, "")
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
        """Reverse the colour's *name*, not the whole row."""
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
        proposed = self._proposal(name)
        measured = f"{ratio:.2f}:1" if ratio is not None else "?"
        if proposed is None:
            settled = (
                "already the palette's own value"
                if self.config.colors.mode == "palette"
                else f"it already meets the {floor}:1 floor"
            )
            note.update(
                Content(
                    f"{name} is {original} at {measured} — {settled}. "
                    "Nothing will be changed."
                )
            )
            return
        if self._is_kept(name):
            note.update(
                Content.from_markup(
                    f"[bold]Rejected.[/] {name} stays {original} "
                    f"{swatch(original)} at {measured}.\n"
                    f"  [dim]u puts it back to {proposed} {swatch(proposed)} "
                    f"{contrast(self.profile, proposed)[1]}[/]"
                )
            )
            return
        if self.config.colors.mode == "palette":
            note.update(
                Content.from_markup(
                    f"{name} is {original} {swatch(original)} at {measured}, and "
                    f"[bold]becomes {proposed}[/] {swatch(proposed)} "
                    f"{contrast(self.profile, proposed)[1]}.\n"
                    "  [dim]The same token every other use of this hue gets, "
                    "drawings included — that is what stops one page holding two "
                    "unrelated blues. k to reject.[/]"
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

    def _proposal(self, name: str) -> str | None:
        """What this run's colour mode would change ``name`` to."""
        return proposal_for(self.profile, name, self.config.colors.mode)

    def action_use(self) -> None:
        """Undo a decision on this row, putting it back to what the run does."""
        name = self.current
        if name is None:
            return
        original = self.profile.colors.originals.get(name, "")
        self.config.colors.reset(name)
        self._message(f"{name}: back to {self._proposal(name)} (was keeping {original})")

    def action_keep(self) -> None:
        name = self.current
        if name is None:
            return
        original = self.profile.colors.originals.get(name, "")
        ratio = contrast(self.profile, original)[0]
        self.config.colors.set(name, original)
        measured = f"{ratio:.2f}:1" if ratio is not None else "?"
        floor = floor_for(self.profile, name)
        if ratio is not None and ratio < floor:
            self._message(
                f"Rejected — {name} stays {original} at {measured}, below the "
                f"{floor}:1 floor. `latexally check` will report it, and the "
                "build will not conform on this point."
            )
        else:
            self._message(
                f"Rejected — {name} stays {original} at {measured}. It clears "
                f"the {floor}:1 floor, so this costs conformance nothing; it "
                "only leaves the colour outside the shared palette."
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
        self.query_one("#colors", DataTable).focus()
        ratio = contrast(self.profile, normalised)[0]
        floor = floor_for(self.profile, name)
        message = f"{name} → {normalised}"
        if ratio is not None and ratio < floor:
            message += (
                f". That is below the {floor}:1 floor. It will be used as "
                "given; `latexally check` will report it."
            )
        self._message(message)

    def _message(self, text: str) -> None:
        self.say("#color-msg", text)
        self.reload()


ALT_DETAIL = {
    "caption": (
        "Adds \\caption{<<TODO:figure-id>>} to every figure and table that has "
        "none, and lists each one in the alt-text worklog.\n\n"
        "The marker is printed on the page, so an unfilled one is impossible "
        "to miss in the PDF. Floats only: a graphic outside a figure or table "
        "is reported and left alone, because \\caption there does not compile."
    ),
    "on": (
        "Writes <<TODO:figure-id>> into the .tex where an author will see it, "
        "and lists each figure in the alt-text worklog.\n\n"
        "Fill them in and run again; a described figure is left alone, so a "
        "second pass changes only what is still outstanding."
    ),
    "off": (
        "Figures are not scanned and no worklog is written. The conversion "
        "still tags structure, headings and math — everything except the "
        "descriptions."
    ),
}


class AltScreen(StepScreen):
    """Figure descriptions: caption them, mark them, or leave them alone."""

    heading = "What should happen to undescribed figures?"

    def body(self) -> Iterator[Widget]:
        alt = self.config.alt
        with Horizontal(classes="row"):
            yield Static("Alt    ↑ ↓", classes="gutter")
            yield Choice(
                Radio(
                    "caption — add a visible \\caption{} to figures that have none",
                    value=alt.captions,
                    name="caption",
                ),
                Radio(
                    "on — mark every undescribed figure",
                    value=alt.scans and not alt.captions,
                    name="on",
                ),
                Radio(
                    "off — skip figures entirely",
                    value=not alt.scans,
                    name="off",
                ),
                id="alt-mode",
            )

    def on_mount(self) -> None:
        self._sync()

    @on(RadioSet.Changed)
    def _changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        pressed = self.query_one("#alt-mode", RadioSet).pressed_button
        mode = pressed.name if pressed else ("on" if self.config.alt.scans else "off")
        label = {"caption": "caption", "on": "on", "off": "off"}[mode]
        self.describe(label, ALT_DETAIL[mode])
        self.config.alt = AltChoice(
            mode={"caption": "caption", "on": "placeholders", "off": "off"}[mode],
            strict=False,
        )


class OutputScreen(StepScreen):
    """Where everything goes, and whether the corpus is edited at all."""

    heading = "Where does the output go?"
    hint = (
        "A blank artifact path restores its default; a relative one hangs off "
        "the root."
    )

    def container(self) -> Widget:
        return VerticalScroll(id="body")

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
        yield Static(
            "edit needs a clean git worktree — it is the only mode that "
            "rewrites course material, and git is what makes that undoable.",
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


class ReviewScreen(StepScreen):
    """Everything this run will do, before anything is written."""

    heading = "Review"
    BINDINGS = [
        Binding("enter", "advance", "Continue", priority=True),
        Binding("n", "next", "Continue", show=False),
    ]

    def container(self) -> Widget:
        return VerticalScroll(id="body")

    @staticmethod
    def section(title: str) -> Iterator[Widget]:
        """A ruled heading. This screen is six unrelated answers in a column."""
        yield Rule(classes="divider")
        yield Static(title, classes="section")

    def body(self) -> Iterator[Widget]:
        yield Static("", id="verdict", classes="heading")
        yield Static("", id="settings", classes="note")

        yield from self.section("Selected directories")
        yield Static("", id="scope-list", classes="note")

        yield from self.section("What gets built")
        yield Static("", id="documents")
        scanning = LoadingIndicator(id="documents-progress")
        scanning.display = False
        yield scanning
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

        yield from self.section("Injected into each driver")
        yield Static("", id="preamble", classes="detail")

    def on_mount(self) -> None:
        from ..build import preamble_for

        config, profile = self.config, self.profile
        count = len(config.assignments)
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
        # `source_files_for` follows every \input a driver reaches, across the
        # whole shared question bank, for every selected assignment. On a scope
        # of any size that is seconds, and it used to run right here -- so the
        # last screen before the build drew itself and then stopped dead.
        self.query_one("#documents", Static).update(Content("reading the sources…"))
        self.query_one("#documents-progress", LoadingIndicator).display = True
        self._scan_documents()
        self.query_one("#colors", Static).update(colors_table(profile, config))
        self.query_one("#output", Static).update(output_table(config))
        self.set_next(bool(count), "Nothing is selected; go back to Scope.")

    @work(thread=True, exclusive=True, group="review-documents")
    def _scan_documents(self) -> None:
        table = self._documents()
        self.app.call_from_thread(self._documents_ready, table)

    def _documents_ready(self, table) -> None:
        self.query_one("#documents-progress", LoadingIndicator).display = False
        self.query_one("#documents", Static).update(table)

    def _alt_text(self, config) -> None:
        """Say what reaches the .tex, in the characters that reach it."""
        from ..apply import PLACEHOLDER

        self.say("#alt", config.alt.describe())
        if not config.alt.injects:
            self.say("#alt-markup", "")
            return
        marker = PLACEHOLDER.format(id="fig-1a2b3c4d")
        caption = (
            f"  \\caption{{{marker}}}   (added to a figure or table with none)\n"
            if self.config.alt.captions
            else ""
        )
        self.say(
            "#alt-markup",
            "Each undescribed figure is wrapped where it stands:\n"
            f"  \\begin{{Described}}{{{marker}}}\n"
            "    …the figure, byte for byte as you wrote it…\n"
            "  \\end{Described}\n"
            f"  \\described{{{marker}}}{{…}}   (for a graphic sharing its line)\n"
            + caption
            + "\n"
            "A figure that already has a description keeps it and is not "
            "touched, so filling markers in and running again changes only "
            "what is still outstanding. Every marker is named in the build log "
            "and reported by `latexally check`.",
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
    """The jobname ``build`` will use -- from ``build`` itself, not a copy."""
    from ..build import accessible_slug, base_slug

    return accessible_slug(base_slug(path, variant))


class BuildScreen(Screen):
    """The run itself, with a row per document and the live LaTeX log."""

    BINDINGS = [
        Binding("b", "start", "Build"),
        Binding("enter", "finish", "Exit", priority=True),
    ]

    _build_started: bool = False
    _build_queue: list[tuple[str, str, str]] = []
    _build_done: bool = False
    _waiting = None

    def compose(self) -> ComposeResult:
        yield Static("", id="build-status", classes="heading")
        spinner = LoadingIndicator(id="build-progress")
        spinner.display = False
        yield spinner
        yield DataTable(id="progress")
        yield Static("Latest log", classes="label")
        yield RichLog(id="log", max_lines=2000)
        yield Static("", id="build-hint", classes="note")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#progress", DataTable)
        table.add_column("assignment")
        table.add_column("document")
        table.add_column("state", width=10)
        for name in ("pages", "bookmarks", "figures"):
            table.add_column(name, width=9)
        table.add_column("pixel diff", width=11)
        self._build_queue = self._queue()
        queue = self._build_queue
        for key, assignment, variant in queue:
            table.add_row(assignment, variant, "queued", "", "", "", "", key=key)
        self._log_path: Path | None = None
        self._log_at = 0
        self.set_interval(0.4, self._tail)

        auto = bool(queue) and self.app.config.output.edits_sources
        self.query_one("#build-status", Static).update(
            "Nothing to build: no assignment resolved from this scope."
            if not queue
            else "Building…"
            if auto
            else f"Ready — {len(queue)} document(s) queued. Press b to build."
        )
        self.query_one("#build-hint", Static).update(
            ""
            if auto
            else "Nothing has been written yet."
            if queue
            else "Go back and choose a scope that names an assignment."
        )
        self.refresh_bindings()
        if auto:
            self.call_after_refresh(self.action_start)

    def action_start(self) -> None:
        """Begin the build. Ignored once it is already running."""
        if self._build_started or not self._build_queue:
            return
        self._build_started = True
        self.app.config = replace(self.app.config, write=True)
        self.app.should_run = True
        self.refresh_bindings()
        self.query_one("#build-status", Static).update("Building…")
        self.query_one("#build-hint", Static).update("")
        # Three LaTeX passes per document, and the log pane stays empty until
        # the first one writes something. Without this the screen says
        # "Building…" and then holds still for a minute.
        self.query_one("#build-progress", LoadingIndicator).display = True
        self.build()

    def action_finish(self) -> None:
        """Leave, once there is nothing still compiling."""
        if self._build_done:
            self.app.exit()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "start":
            return None if self._build_started or not self._build_queue else True
        if action == "finish":
            return True if self._build_done else None
        return super().check_action(action, parameters)

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
            pass

    def _started(self, assignment, variant: str) -> None:
        key = f"{assignment.path}|{variant}"
        self._row(key, None, None, "building…")
        jobs = self.app.config.jobs
        if jobs > 1:
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
            f"{100 * report.pixel_diff:.2f}%"
            if report.pixel_diff is not None
            else (report.diff_note or "—"),
        )

    def _tail(self) -> None:
        """Follow the LaTeX log of whatever is compiling right now."""
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


    def _failed(self, message: str) -> None:
        self.app.reports = []
        self.query_one("#build-status", Static).update(Content(f"Failed: {message}"))
        if "uncommitted change" in message:
            # Not a failed build: a precondition, and one the user is probably
            # fixing in another window this second. Making them quit the runner
            # and walk all seven screens again to be asked a second time is the
            # kind of dead end this screen exists to avoid, so it watches the
            # worktree and starts itself when it comes back clean.
            self._build_started = False
            self.query_one("#build-progress", LoadingIndicator).display = False
            self.query_one("#build-hint", Static).update(
                Content("Commit or stash them and this starts itself — or press b.")
            )
            self.refresh_bindings()
            self._waiting = self.set_interval(2, self._check_worktree)
            return
        self._offer_exit()

    @work(thread=True, exclusive=True, group="worktree")
    def _check_worktree(self) -> None:
        """Is the corpus clean yet? A `git status`, so not on the message pump."""
        from ..build import require_clean_worktree

        try:
            require_clean_worktree(
                self.app.profile.corpus.root.resolve(),
                ignore=self.app.config.output.root,
            )
        except LatexAllyError:
            return
        self.app.call_from_thread(self._worktree_clean)

    def _worktree_clean(self) -> None:
        if self._build_started or self._waiting is None:
            return
        self._waiting.stop()
        self._waiting = None
        self.action_start()

    def _done(self, reports, descriptions) -> None:
        from ..cli import _report_table

        self.app.reports = reports
        self.app.descriptions = descriptions
        failures = [report for report in reports if not report.ok]
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
        self._build_done = True
        self.query_one("#build-progress", LoadingIndicator).display = False
        self.refresh_bindings()
        saved = self.app.config.output.root / "build-log.txt"
        where = f"written to {show_path(saved)}\n" if saved.is_file() else ""
        self.query_one("#build-hint", Static).update(
            Content(f"{where}Done — press Enter to exit.")
        )


class RevertScreen(Screen):
    """Undo a run, after showing exactly what that means."""

    BINDINGS = [
        Binding("backslash", "close", "Back", key_display="\\"),
        Binding("y", "confirm", "Yes, revert"),
        Binding("enter", "refuse", "", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Revert", id="revert-heading", classes="heading")
        yield Static(
            "Puts your .tex back with git and deletes what this tool wrote. "
            "Files it does not recognise are left alone.",
            classes="hint",
        )
        with VerticalScroll(id="body"):
            yield Static("", id="revert-plan")
        spinner = LoadingIndicator(id="revert-progress")
        spinner.display = False
        yield spinner
        yield Static("", id="revert-note", classes="reason")
        yield Footer()

    _reverting: bool = False

    def on_mount(self) -> None:
        self._plan = None
        self._load()

    def _heading(self, text: str) -> None:
        self.query_one("#revert-heading", Static).update(Content(text))

    def action_refuse(self) -> None:
        """Enter, which means Next everywhere else and must not mean this."""
        if self._reverting:
            return
        if self._plan is None or self._plan.empty:
            self.say("Nothing to revert.")
            return
        self.say(
            "Enter does not revert — this rewrites your course material. "
            "Press y to go ahead, or \\ to go back."
        )

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "confirm":
            if self._reverting or self._plan is None or self._plan.empty:
                return None
            return True
        if action == "revert":
            return False
        return True

    def _load(self) -> None:
        """Read the plan off the UI thread."""
        self._heading("Revert — reading the plan…")
        self.query_one("#revert-progress", LoadingIndicator).display = True
        self.refresh_bindings()
        self._plan_revert()

    @work(thread=True, exclusive=True, group="revert-plan")
    def _plan_revert(self) -> None:
        from ..revert import plan_revert

        try:
            plan = plan_revert(self.app.config, self.app.profile)
        except LatexAllyError as exc:
            self.app.call_from_thread(self._planned, None, str(exc))
            return
        self.app.call_from_thread(self._planned, plan, "")

    def _planned(self, plan, error: str) -> None:
        self._plan = plan
        if not self._reverting:
            self.query_one("#revert-progress", LoadingIndicator).display = False
        pane = self.query_one("#revert-plan", Static)
        if error:
            self._heading("Revert")
            pane.update(Content(error))
        else:
            self._heading("Revert" if plan.empty else "Confirm revert (y)")
            pane.update(self._describe(plan))
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
        """Start the revert. The work happens off the UI thread."""
        if self._reverting or self._plan is None or self._plan.empty:
            return
        self._reverting = True
        self.refresh_bindings()
        self.query_one("#revert-progress", LoadingIndicator).display = True
        self.say(
            f"Reverting {len(self._plan.restore)} restored, "
            f"{len(self._plan.remove)} deleted, "
            f"{len(self._plan.outputs)} output path(s)…"
        )
        self._run_revert()

    @work(thread=True, exclusive=True)
    def _run_revert(self) -> None:
        from ..revert import do_revert

        plan = self._plan
        try:
            do_revert(plan)
        except LatexAllyError as exc:
            self.app.call_from_thread(self._reverted, plan, str(exc))
            return
        self.app.call_from_thread(self._reverted, plan, "")

    def _reverted(self, plan, error: str) -> None:
        self._reverting = False
        self.query_one("#revert-progress", LoadingIndicator).display = False
        if error:
            self.say(error)
        else:
            self.say(
                f"Reverted: {len(plan.restore)} restored, "
                f"{len(plan.remove)} deleted. \\ to go back."
            )
        self._load()
        self.refresh_bindings()

    def say(self, text: str) -> None:
        note = self.query_one("#revert-note", Static)
        note.update(Content(text))
        note.display = bool(text)


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
    /* One green, named once. Every accent in the runner is this colour, so
       retuning it is one edit rather than a hunt through the stylesheet. */
    $ally-green: #1b5e20;
    /* The selected option's explanation. Docked bottom on every step so it
       occupies the same rows throughout, and bordered so it reads as the
       answer to the row above rather than as more of the list. */
    .detail-box {
        /* NOT `dock: bottom`: the Footer is docked there too, and the last row
           of the box -- its bottom border -- ended up underneath it. `#body`
           is already 1fr, so a plain last child before the Footer lands on the
           same rows on every step without fighting it for them. */
        height: auto;
        max-height: 10;
        margin: 1 1 0 1;
        padding: 0 1;
        border: round $ally-green;
        border-title-color: $ally-green;
        border-title-style: bold;
        color: $text-muted;
    }
    RadioSet > Radio.-selected > .toggle--label { color: $ally-green; }
    Radio > .toggle--button { color: $ally-green; }
    SelectionList > .selection-list--button-selected,
    SelectionList > .selection-list--button-selected-highlighted {
        color: $ally-green;
    }
    .heading { text-style: bold; }
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
    /* The keypress blink. Reverse video rather than a colour, for the same
       reason the table cursor is: it shows up on whatever palette the terminal
       has, including the ones where $ally-green is unreadable. */
    Footer.-keyed { text-style: reverse; }
    """

    def __init__(
        self,
        profile: Profile,
        config: RunConfig | None = None,
        *,
        ask_profile: bool = False,
        corpus_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.theme = "ansi-light"
        self.profile = profile
        self.config = config or RunConfig(profile=profile.name)
        self.config.output.anchor(profile)
        self.here_scope = scope_from_cwd(profile)
        self.scope_mode = "local" if self.here_scope is not None else "choose"
        self._corpus_root = corpus_root
        self.profile_names = builtin_profile_names()
        self.ask_profile = ask_profile and len(self.profile_names) > 1
        self.active_steps = (ProfileScreen, *STEPS) if self.ask_profile else STEPS
        self.should_run = False
        self.reports: list = []
        self.descriptions: dict = {}
        self._index = 0

    def on_mount(self) -> None:
        self.push_screen(self.active_steps[0]())

    async def on_event(self, event: events.Event) -> None:
        """Blink the footer on every key, so a key that changes nothing on the"""
        if isinstance(event, events.Key):
            self._blink()
        await super().on_event(event)

    def _blink(self) -> None:
        footers = self.screen.query(Footer)
        footers.add_class("-keyed")
        self.set_timer(0.12, lambda: footers.remove_class("-keyed"))

    active_steps: tuple[type[StepScreen], ...] = STEPS

    def use_profile(self, name: str) -> None:
        """Switch course, and re-derive everything that was read off the old one."""
        if name == self.profile.name:
            return
        if name not in self.profile_names:
            raise LatexAllyError(f"no such profile: {name}")
        self.profile = load_profile(name, corpus_root=self._corpus_root)
        self.config.profile = name
        self.config.output.anchor(self.profile)
        self.here_scope = scope_from_cwd(self.profile)
        self.scope_mode = "local" if self.here_scope is not None else "choose"

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
        """Show the queue. Does NOT set `write`, and does not build."""
        self.push_screen(BuildScreen())

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Hide `r` while the revert screen is the one showing."""
        if action == "revert" and isinstance(self.screen, RevertScreen):
            return False
        return super().check_action(action, parameters)

    def action_revert(self) -> None:
        """Undo a run. Available from every step, including after the build."""
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
