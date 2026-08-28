"""What Canvas Ally actually produces: the alternative formats, and their text.

Why this tier exists
--------------------

``check_pdf_structure`` reads the artefact and confirms the ``/Alt`` a reader
*should* hear. It cannot confirm the one a student *does* hear, because that
depends entirely on which text extractor sits downstream -- and the extractors
disagree. Measured on one spec-correct build of ``fa26/dis/00B``, on the single
formula ``$a = 1 - i\\sqrt{3}$``:

===============  ==========================================================
structure tree   ``a is equal to, 1 minus, i times the square root of 3``
PDFBox           ``a is equal to, 1 minus, i times the square root of 3``
PyMuPDF          ``a is \\ne\\nqual to, 1 minus, ... of \\n3``  (fragmented)
poppler          ``1−i 3``            (``/ActualText`` ignored)
Ghostscript      ``Let a = 1−i √3``   (``/ActualText`` ignored)
===============  ==========================================================

The PDF is right in all five cases. Three of the five readers are wrong, and
nothing in the tool noticed, because nothing in the tool had ever looked at
extracted text. That is the whole of "text to speech is not working and it is
hard to validate".

Canvas Ally is a Java service built on PDFBox, so PDFBox is the extractor whose
answer is the student's answer, and it is weighted accordingly by
``ALLY-FMT-001``. The other four still run: a disagreement is worth reporting
even when the one that matters passes, because "works in Ally, silent in
Preview" is a support ticket waiting to happen, and because a future Ally may
swap engines.

What this module does NOT do
----------------------------

It does not synthesise the shipped MP3 or BRF -- Ally does that, on its own
servers, from its own voices and tables. ``say`` and ``lou_translate`` here are
*evidence*: an artefact a human can play or emboss to settle an argument that a
transcript diff cannot. Both are rule-based and offline, so
``tests/test_no_ai_in_production.py`` stays true.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .content import normalise
from .rules import Finding, Severity
from .speech import spoken_utterances
from .structure import PdfStructure, read_structure

__all__ = [
    "Extraction",
    "extract_all",
    "check_formats",
    "render_audio",
    "render_braille",
    "write_evidence",
]

_TIMEOUT = 300

def _pdfbox_jar() -> Path:
    """Where the vendored PDFBox lives.

    Vendored rather than resolved from PATH: the point of this extractor is to
    reproduce one specific downstream engine, and "whatever pdfbox the machine
    happens to have" is not that. The env var is for a non-editable install,
    where the repo's ``vendor/`` is not next to the package.
    """
    override = os.environ.get("LATEXALLY_PDFBOX_JAR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "vendor" / "pdfbox" / "pdfbox-app.jar"

#: The extractor whose answer is the student's answer. Canvas Ally is a Java
#: service and PDFBox is its text layer, so a failure here is a failure a
#: student experiences; a failure anywhere else is a warning about reader
#: portability. Change this only with a measured Ally round-trip in hand.
ALLY_EXTRACTOR = "pdfbox"

#: Raw LaTeX that survived into a transcript. Any of these means a reader is
#: about to announce backslashes: latex-lab's default ``/Alt`` template is
#: exactly this, and it passes veraPDF.
_RAW_LATEX = re.compile(r"\\(?:frac|sqrt|begin|end|mathrm|left|right)\b|\$|LaTeX formula")

#: A run of bare numbers and axis words with nothing else between them. This is
#: what an undescribed pgfplots figure extracts as -- "−2 −1 1 2 3 4 −3 −2 −1 1
#: 2 3 Re Im" -- and it is indistinguishable from data to a reader, which is
#: worse than silence: silence at least prompts a question.
_GLYPH_SOUP = re.compile(
    r"(?:(?:[-−+]?\d+(?:\.\d+)?|Re|Im|[xy])[\s,]+){6,}", re.IGNORECASE
)


@dataclass(slots=True)
class Extraction:
    """One extractor's answer, or the reason there isn't one."""

    name: str
    #: What the extractor read, whitespace-normalised. Empty when unavailable.
    text: str = ""
    #: Why this extractor did not run. Empty when it did.
    skipped: str = ""

    @property
    def ran(self) -> bool:
        return not self.skipped

    def says(self, phrase: str) -> bool:
        """Whether ``phrase`` survives into this extractor's output.

        Compared on the space-squeezed forms of both. Kerning splits a rendered
        word into several show-text operators, so "pixel" arrives as "pix el"
        and a substring test on the raw text fails on correct output.
        """
        return _squeeze(phrase) in _squeeze(self.text)


def _squeeze(text: str) -> str:
    """Normalised and space-free, for comparison only -- never for display."""
    return "".join(normalise(text).split()).lower()


# ---------------------------------------------------------------------- #
# the extractor matrix
# ---------------------------------------------------------------------- #


def _run(command: list[str]) -> str | None:
    """Run an extractor and return its stdout, or ``None`` if it did not work.

    ``stdin=DEVNULL`` is not tidiness. Ghostscript reads stdin when it is a pipe
    it can read, and under pytest -- where stdin is captured rather than a
    terminal -- ``gs -sDEVICE=txtwrite`` sat waiting for input that was never
    coming. The whole suite hung on one test with no output and no timeout,
    because ``subprocess.timeout`` was never reached: the process was alive and
    healthy, just blocked. A checker must never wait on a keyboard.
    """
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


@contextmanager
def _scratch(pdf: Path, suffix: str):
    """A private file for an extractor that cannot write to stdout.

    In a temp directory, never beside the input, and that is not fastidiousness.
    A killed run leaves the file behind, and the next `pdfbox export:text` finds
    its output path occupied and asks -- on stdin -- whether to overwrite. With
    stdin closed it blocks anyway, which presents as the whole test suite
    hanging silently on whichever test happens to run third. Writing into the
    corpus was also simply wrong: `latexally check` is a read-only command.
    """
    with tempfile.TemporaryDirectory(prefix="latexally-formats-") as directory:
        yield Path(directory) / (pdf.stem + suffix)


def _extract_structure(pdf: Path, structure: PdfStructure | None) -> Extraction:
    """The tag tree, walked as a screen reader walks it. JAWS, NVDA, VoiceOver."""
    try:
        said = spoken_utterances(pdf, structure=structure)
    except Exception as exc:  # noqa: BLE001 - a bad tree is a finding, not a crash
        return Extraction("structure", skipped=f"structure tree unreadable: {exc}")
    if not said:
        return Extraction("structure", skipped="not tagged, so there is no tree to walk")
    return Extraction("structure", text=normalise(" ".join(item.text for item in said)))


def _extract_pdfbox(pdf: Path) -> Extraction:
    """PDFBox ``PDFTextStripper`` -- Canvas Ally's text layer."""
    if not shutil.which("java"):
        return Extraction("pdfbox", skipped="java not found; install a JRE 11 or newer")
    jar = _pdfbox_jar()
    if not jar.is_file():
        return Extraction(
            "pdfbox",
            skipped=(
                f"vendored jar missing: {jar} — run ./vendor/pdfbox/fetch.sh, "
                "or point LATEXALLY_PDFBOX_JAR at one"
            ),
        )
    # `-o -` is not supported, so the text goes to a file and is read back.
    with _scratch(pdf, ".pdfbox.txt") as out:
        result = _run(
            ["java", "-jar", str(jar), "export:text", "-i", str(pdf), "-o", str(out)]
        )
        if result is None or not out.is_file():
            return Extraction("pdfbox", skipped="pdfbox export:text failed")
        text = out.read_text(encoding="utf-8", errors="replace")
    return Extraction("pdfbox", text=normalise(text))


#: Run in a fresh interpreter rather than imported. PyMuPDF's C extension
#: SEGFAULTS when it is loaded into a process that has already loaded pikepdf --
#: which this module always has, because `read_structure` runs first and pikepdf
#: is how it reads the tag tree. The crash is in `pymupdf._extra`'s
#: `create_module`, it takes the whole interpreter with it, and it presents as a
#: silent hang under pytest rather than as an error. Nothing here can fix a
#: native library conflict; a subprocess simply does not have it, and four of
#: the five extractors are subprocesses anyway.
_MUPDF_SCRIPT = """
import sys
import pymupdf
with pymupdf.open(sys.argv[1]) as document:
    sys.stdout.write(" ".join(page.get_text() for page in document))
"""


def _extract_mupdf(pdf: Path) -> Extraction:
    """PyMuPDF -- stands for macOS Preview, pdf.js and the built-in readers."""
    text = _run([sys.executable, "-c", _MUPDF_SCRIPT, str(pdf)])
    if text is None:
        return Extraction(
            "mupdf", skipped="PyMuPDF could not read it (extra: tui)"
        )
    return Extraction("mupdf", text=normalise(text))


def _extract_poppler(pdf: Path) -> Extraction:
    """poppler ``pdftotext`` -- most Linux and CI pipelines."""
    if not shutil.which("pdftotext"):
        return Extraction("poppler", skipped="pdftotext not found; brew install poppler")
    text = _run(["pdftotext", "-nopgbrk", "-q", str(pdf), "-"])
    if text is None:
        return Extraction("poppler", skipped="pdftotext failed")
    return Extraction("poppler", text=normalise(text))


def _extract_ghostscript(pdf: Path) -> Extraction:
    """Ghostscript ``txtwrite`` -- the floor. Ignores /ActualText entirely."""
    binary = shutil.which("gs") or shutil.which("gswin64c")
    if not binary:
        return Extraction("ghostscript", skipped="gs not found; brew install ghostscript")
    with _scratch(pdf, ".gs.txt") as out:
        result = _run(
            [binary, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=txtwrite",
             "-o", str(out), str(pdf)]
        )
        if result is None or not out.is_file():
            return Extraction("ghostscript", skipped="gs txtwrite failed")
        text = out.read_text(encoding="utf-8", errors="replace")
    return Extraction("ghostscript", text=normalise(text))


def extract_all(pdf: Path | str, *, structure: PdfStructure | None = None) -> dict[str, Extraction]:
    """Every extractor's answer for one PDF, keyed by name.

    Ordered most authoritative first, so a report reads top-down from "what the
    student gets" to "what the worst reader gets".
    """
    pdf = Path(pdf)
    found = [
        _extract_structure(pdf, structure),
        _extract_pdfbox(pdf),
        _extract_mupdf(pdf),
        _extract_poppler(pdf),
        _extract_ghostscript(pdf),
    ]
    return {item.name: item for item in found}


# ---------------------------------------------------------------------- #
# renderers -- evidence, not the shipped artefact
# ---------------------------------------------------------------------- #


def render_audio(text: str, destination: Path | str) -> str:
    """Speak ``text`` into an MP3. Returns "" on success, else why not.

    ``say`` is macOS' offline synthesiser and writes AIFF only, so ffmpeg does
    the container. Neither is what Ally uses; the point is a file a human can
    play to settle "does it actually say the description".
    """
    destination = Path(destination)
    if not shutil.which("say"):
        return "say not found (macOS only); no audio rendered"
    if not shutil.which("ffmpeg"):
        return "ffmpeg not found; brew install ffmpeg"
    aiff = destination.with_suffix(".aiff")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _run(["say", "-v", "Samantha", "-o", str(aiff), text]) is None:
        return "say failed"
    try:
        if _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-q:a", "4",
                 str(destination)]) is None:
            return "ffmpeg failed"
    finally:
        aiff.unlink(missing_ok=True)
    return ""


def render_braille(text: str, destination: Path | str, *, table: str = "en-ueb-g2.ctb") -> str:
    """Translate ``text`` to a BRF. Returns "" on success, else why not."""
    destination = Path(destination)
    if not shutil.which("lou_translate"):
        return "lou_translate not found; brew install liblouis"
    try:
        done = subprocess.run(
            ["lou_translate", table],
            input=text,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"lou_translate failed: {exc}"
    if done.returncode != 0:
        return f"lou_translate failed: {done.stderr.strip()[:200]}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(done.stdout, encoding="utf-8")
    return ""


# ---------------------------------------------------------------------- #
# the rules
# ---------------------------------------------------------------------- #


def _described(structure: PdfStructure) -> list[tuple[str, str]]:
    """``(tag, alt)`` for every element carrying substitute text."""
    return [
        (node.tag, normalise(node.alt))
        for node in structure.nodes
        if node.alt and normalise(node.alt)
    ]


def check_formats(
    pdf: Path | str,
    *,
    extractions: dict[str, Extraction] | None = None,
    structure: PdfStructure | None = None,
) -> list[Finding]:
    """Assert on what the downstream formats will contain.

    Every finding names the extractor, because "the speech is missing" and "the
    speech is missing *in Ghostscript*" are different bugs with different
    owners, and conflating them is how the last round of this got called fixed.

    ``extractions`` and ``structure`` let a caller that has already read the PDF
    hand the work over. Not an optimisation for its own sake: each extractor is
    a subprocess and PDFBox is a JVM start, so a caller that both writes the
    evidence and checks it was paying for two full matrices -- ten processes
    where five would do.
    """
    pdf = Path(pdf)
    findings: list[Finding] = []
    structure = structure or read_structure(pdf)
    if not structure.tagged:
        return [
            Finding(
                "ALLY-FMT-001",
                Severity.ERROR,
                "the PDF is not tagged, so no alternative format can carry a description",
                file=str(pdf),
                standard="WCAG 1.1.1",
                hint="build with tagging on; `latexally doctor` reports the mode",
            )
        ]

    extractions = extractions or extract_all(pdf, structure=structure)
    described = _described(structure)
    ally = extractions.get(ALLY_EXTRACTOR)

    # ALLY-FMT-001 -- the description never reaches the student.
    if ally is not None and ally.ran:
        for tag, alt in described:
            if ally.says(alt):
                continue
            findings.append(
                Finding(
                    "ALLY-FMT-001",
                    Severity.ERROR,
                    f"{tag} description is absent from the {ALLY_EXTRACTOR} text layer, "
                    f"so Ally's MP3 and braille will not contain it: {alt[:90]!r}",
                    file=str(pdf),
                    standard="WCAG 1.1.1",
                    hint=(
                        "the description is in the structure tree only; it also needs to "
                        "reach the content stream as /ActualText on the marked content"
                    ),
                    data={"tag": tag, "alt": alt, "extractor": ALLY_EXTRACTOR},
                )
            )

    # ALLY-FMT-002 -- extractors disagree about the same file.
    for name, extraction in extractions.items():
        if name == ALLY_EXTRACTOR or not extraction.ran:
            continue
        missing = [alt for _, alt in described if not extraction.says(alt)]
        if not missing:
            continue
        findings.append(
            Finding(
                "ALLY-FMT-002",
                Severity.WARNING,
                f"{name} loses {len(missing)} of {len(described)} descriptions that "
                f"{ALLY_EXTRACTOR} keeps; a reader on that engine hears the raw glyphs",
                file=str(pdf),
                standard="WCAG 1.1.1",
                hint=(
                    "not a defect in this PDF -- poppler and Ghostscript ignore "
                    "/ActualText by design. Reported so a reader-portability claim is "
                    "never made on untested ground."
                ),
                data={"extractor": name, "missing": len(missing), "total": len(described)},
            )
        )

    # ALLY-FMT-003 -- raw LaTeX survived into a transcript.
    for name, extraction in extractions.items():
        if not extraction.ran:
            continue
        found = _RAW_LATEX.search(extraction.text)
        if not found:
            continue
        findings.append(
            Finding(
                "ALLY-FMT-003",
                Severity.ERROR,
                f"{name} transcript contains raw LaTeX, which is announced "
                f"character by character: {found.group(0)!r}",
                file=str(pdf),
                standard="WCAG 1.1.1",
                hint=(
                    "latex-lab's default math /Alt is the LaTeX source; load "
                    "latexally-math and build with math_speech on"
                ),
                data={"extractor": name, "match": found.group(0)},
            )
        )

    # ALLY-FMT-010 -- an undescribed drawing extracting as bare numbers.
    if ally is not None and ally.ran:
        for run in _GLYPH_SOUP.findall(ally.text):
            findings.append(
                Finding(
                    "ALLY-FMT-010",
                    Severity.ERROR,
                    "a drawing's axis labels reach the text layer as bare numbers, with "
                    f"no description to replace them: {normalise(run)[:70]!r}…",
                    file=str(pdf),
                    standard="WCAG 1.1.1",
                    hint=(
                        "the figure emitted no Figure element; describe it in the "
                        "worklog so /Alt and /ActualText replace the drawing"
                    ),
                    data={"extractor": ALLY_EXTRACTOR, "run": normalise(run)[:200]},
                )
            )

    return findings


# ---------------------------------------------------------------------- #
# evidence on disk
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class Evidence:
    """Where the run put what a human can inspect."""

    directory: Path
    transcripts: dict[str, Path] = field(default_factory=dict)
    audio: Path | None = None
    braille: Path | None = None
    notes: list[str] = field(default_factory=list)


def write_evidence(
    pdf: Path | str, directory: Path | str, *, audio: bool = True, braille: bool = True
) -> Evidence:
    """Write every transcript, plus an MP3 and a BRF, under ``directory``."""
    pdf = Path(pdf)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(directory=directory)

    structure = read_structure(pdf)
    extractions = extract_all(pdf, structure=structure)
    for name, extraction in extractions.items():
        target = directory / f"transcript-{name}.txt"
        target.write_text(
            extraction.text if extraction.ran else f"[skipped] {extraction.skipped}\n",
            encoding="utf-8",
        )
        evidence.transcripts[name] = target
        if not extraction.ran:
            evidence.notes.append(f"{name}: {extraction.skipped}")

    # The Ally transcript is what gets spoken and embossed: rendering the
    # structure walk instead would prove the tag tree is fine while saying
    # nothing about the artefact a student downloads.
    source = extractions.get(ALLY_EXTRACTOR)
    spoken = source.text if source is not None and source.ran else ""
    if spoken and audio:
        target = directory / "speech.mp3"
        problem = render_audio(spoken, target)
        if problem:
            evidence.notes.append(f"audio: {problem}")
        else:
            evidence.audio = target
    if spoken and braille:
        target = directory / "braille.brf"
        problem = render_braille(spoken, target)
        if problem:
            evidence.notes.append(f"braille: {problem}")
        else:
            evidence.braille = target

    report = {
        "pdf": str(pdf),
        "ally_extractor": ALLY_EXTRACTOR,
        "extractors": {
            name: {"ran": item.ran, "skipped": item.skipped, "characters": len(item.text)}
            for name, item in extractions.items()
        },
        "findings": [
            item.as_dict()
            for item in check_formats(pdf, extractions=extractions, structure=structure)
        ],
        "notes": evidence.notes,
    }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return evidence
