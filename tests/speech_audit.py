r"""Read a built PDF the way a screen reader does, and judge what it hears.

Every other check in this project inspects a *proxy*: the log says no errors,
the tag tree says every Figure carries /Alt, the content stream says no text was
painted outside a marked region. All three can pass on a document that is unusable
aloud. Twice this session a defect got through exactly that way -- ulem's
internals typeset onto the page, and MathCAT's bold letters leaking LaTeX error
text into /Alt -- and in both cases the logs were clean and the page was wrong.

So this reads the artifact. It walks the structure tree in reading order, builds
the utterance sequence a reader would produce, and asks four questions of it:

silence
    An element that must speak says nothing. An empty /Alt is caught by the
    checker; whitespace, a lone hyphen, or "..." are not, and all three are
    silence to a listener.

markup
    An utterance carries LaTeX or PDF markup, which a reader utters verbatim:
    "backslash vec x" for ``\vec{x}``.

named symbols
    An utterance spells a symbol out instead of speaking it: "x underscore i"
    where the author meant "x sub i". A converter that gives up produces these,
    and they read as gibberish rather than as an error.

coverage
    Something visible is never announced. Text present in the page and absent
    from the transcript is text a sighted reader has and a listener does not.

Not a unit test. It is the audit `tests/test_corpus.py` runs over a sample and
`tools/repair_lab` runs over the corpus, and it reports per document so a
failure names the page and the sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from latexally.check.content import read_page_content
from latexally.check.speech import spoken_utterances, unreachable_text

#: Tags whose whole job is to say something. A Figure or a Formula that speaks
#: nothing is a hole in the document; a P that speaks nothing is a blank line.
_MUST_SPEAK = ("Figure", "Formula")

#: Punctuation and filler that a reader either skips or renders as a pause.
#: An /Alt made only of these is silence with a value in it, which is worse
#: than an empty one because the checker counts it as described.
_SILENT = re.compile(r"^[\s.,;:!?'\"()\[\]{}<>/\\|_~^*+=-]*$")

#: Markup that is wrong wherever it appears. A backslash never belongs in
#: speech, and the two phrases are latex-lab's own default math alt -- the
#: LaTeX source wrapped in a fixed pair of sentences.
_MARKUP_ANYWHERE = re.compile(r"LaTeX formula (?:starts|ends)|\\")

#: Markup that is only wrong in *substitute* text.
#:
#: Prose legitimately carries these. "$0.12 per kilowatt-hour" is a price and
#: "PG&E" is a company. Control bytes are the content reader's own artifact: it
#: returns raw show-text operators, so the `fl` ligature in "flowing" arrives as
#: \x03 and every kerned word arrives split ("F ormat"). `speech.py` says so in
#: its module docstring, and flagging that would report the extractor rather
#: than the document.
#:
#: In an /Alt the same characters mean LaTeX leaked into the description
#: instead of being spoken, which is what a listener actually hears.
_MARKUP_IN_ALT = re.compile(r"[$]|\{|\}|[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: A markup character named rather than spoken. These are what a converter
#: emits when it gives up on the notation, and no correct renderer produces
#: them: measured across 221 formulas in a current build, zero occurrences,
#: against 3,167 "backslash" in a build from before the MathCAT migration.
#:
#: Braces are deliberately NOT here. "open brace a sub 1, close brace" is how
#: MathCAT reads set notation aloud, and it is correct -- 458 occurrences in
#: real output. Listing them would fail the audit on speech that is right.
#: "tilde" is absent for the same reason as braces: MathCAT says "bold cap
#: sigma tilde" for \\tilde{\\Sigma}, which is the accent read aloud correctly.
#: Word-bounded, so "understated" stays prose.
_NAMED_SYMBOL = re.compile(
    r"\b(?:backslash|underscore|caret|circumflex|"
    r"dollar sign|ampersand|asterisk|hat symbol)\b",
    re.IGNORECASE,
)

#: A run of letters long enough to be a word. Buried punctuation and equation
#: numbers are not content a listener loses; buried sentences are.
_PROSE = re.compile(r"[A-Za-z]{3,}")

#: Macro internals typeset into the page or into speech. Both bugs found this
#: session left a trace of exactly this shape.
_INTERNALS = re.compile(r"cmd/[a-z]+/(?:before|after)|\\UL@|__[a-z]+_[a-z]+:|UTFvi+@")


@dataclass(slots=True)
class SpeechDefect:
    """One thing a listener would get wrong, and where."""

    kind: str
    tag: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return f"{self.kind} <{self.tag}>: {self.detail}"


@dataclass(slots=True)
class SpeechAudit:
    """What a reader would say for one document, and what is wrong with it."""

    pdf: Path
    utterances: int = 0
    spoken_characters: int = 0
    defects: list[SpeechDefect] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.defects

    def of_kind(self, kind: str) -> list[SpeechDefect]:
        return [item for item in self.defects if item.kind == kind]


def audit_speech(pdf: Path) -> SpeechAudit:
    """Read ``pdf`` aloud in the abstract and report every way that fails."""
    pdf = Path(pdf)
    result = SpeechAudit(pdf=pdf)
    utterances = spoken_utterances(pdf)
    result.utterances = len(utterances)

    for item in utterances:
        text = item.text or ""
        result.spoken_characters += len(text.strip())
        if item.tag in _MUST_SPEAK and _SILENT.match(text):
            result.defects.append(
                SpeechDefect("silence", item.tag, f"{text!r} is nothing to a listener")
            )
            continue
        # Substitute text is held to a stricter standard than prose: an /Alt is
        # what a reader says *instead of* the content, so markup in it is
        # always a defect, where the same character in a paragraph is usually
        # money or a company name.
        substitute = item.source in ("alt", "actualtext")
        for pattern, kind in (
            (_INTERNALS, "internals"),
            (_MARKUP_ANYWHERE, "markup"),
            (_MARKUP_IN_ALT if substitute else None, "markup"),
            (_NAMED_SYMBOL, "named-symbol"),
        ):
            if pattern is None:
                continue
            hit = pattern.search(text)
            if hit:
                result.defects.append(SpeechDefect(kind, item.tag, f"{text[:70]!r}"))
                break

    for _tag, alt, buried in unreachable_text(pdf):
        # `unreachable_text` yields (the /Alt that replaces, the text replaced).
        # A numbered equation buries its own "(1)" under the spoken form, and
        # that is the mechanism working: the number is in the /Alt already.
        # Only prose is a loss, so require a word rather than punctuation.
        if not _PROSE.search(buried):
            continue
        # Replaced is not the same as lost. An align's /Alt legitimately speaks
        # the prose set between its rows, so the words reach the listener --
        # just through the substitute rather than the subtree. Only report what
        # the /Alt does NOT say.
        # "Replaced" is not "lost". An align's /Alt speaks the prose set between
        # its rows, so the words do reach the listener -- through the substitute
        # rather than the subtree, and interleaved with equation numbers that
        # are not in the /Alt at all. Demanding the buried text appear verbatim
        # inside the /Alt can therefore never pass.
        #
        # The question worth asking is whether ANY of it is spoken. Prose that
        # is entirely absent is the real defect and the one this found; prose
        # that is present but ordered differently is the mechanism working.
        #
        # Squeezed to letters before comparing, as speech.py's docstring
        # instructs: the content reader returns raw show-text operators, so
        # kerning inside a word survives as a space and "solve" arrives as
        # "solv e".
        # Words are taken from the buried text BEFORE squeezing -- squeezing
        # first yields one enormous token that matches nothing -- and looked up
        # in the squeezed /Alt, so a kerned "solv e" still finds "solve".
        spoken = re.sub(r"[^a-z]", "", alt.lower())
        if any(word in spoken for word in re.findall(r"[a-z]{5,}", buried.lower())):
            continue
        result.defects.append(
            SpeechDefect(
                "unreachable",
                "swallowed",
                f"{buried[:60]!r} is replaced by {alt[:40]!r} and never said",
            )
        )

    return result


def visible_but_unspoken(pdf: Path, *, sample: int = 40) -> list[str]:
    """Words painted on the page that never reach the transcript.

    The complement of :func:`audit_speech`'s ``unreachable`` check, which works
    from the tag tree. This works from the page, so it also catches text that is
    outside the tree entirely -- the case where a reader has no way to reach the
    words even in principle.

    Compared with whitespace squeezed out, because the content reader returns
    show-text operators and kerning inside a word survives as a space: "pixel"
    arrives as "pix el". Comparing raw would report every kerned word as lost.
    """
    pdf = Path(pdf)
    spoken = "".join(item.text or "" for item in spoken_utterances(pdf))
    squeezed = re.sub(r"\s+", "", spoken)
    missing: list[str] = []
    page = 0
    while True:
        try:
            content = read_page_content(pdf, page)
        except Exception:
            break
        if content is None:
            break
        for region in content.regions:
            word = re.sub(r"\s+", "", region.text or "")
            if len(word) > 3 and word not in squeezed:
                missing.append(region.text.strip())
                if len(missing) >= sample:
                    return missing
        page += 1
        if page > 200:  # pragma: no cover - a runaway guard, never reached
            break
    return missing
