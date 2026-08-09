"""latexa11y — LaTeX to ADA Title II / PDF-UA accessibility toolkit.

The package is organised as a pipeline, and each stage is importable on its own:

    texlex      byte-faithful LaTeX source scanning and editing
    scan        locate figures, tables, math and headings in a corpus
    describe    deterministic (non-AI) description skeletons
    catalog     content-addressed store + Markdown worklogs
    apply       write accessibility markup back into .tex
    build       parallel latexmk driver
    check       source lint, log scan, PDF structure assertions, veraPDF
    mathspeech  LaTeX -> MathML -> speech
    agent       machine-readable task API for LLM agents
    tui         Textual review app
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
