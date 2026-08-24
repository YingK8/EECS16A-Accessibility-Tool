"""latexally — LaTeX to ADA Title II / PDF-UA accessibility toolkit.

The package is organised as a pipeline, and each stage is importable on its own:

    texlex      byte-faithful LaTeX source scanning and editing
    run         what a run does: standards, colours, output layout
    discover    what it does it to: assignments, drivers, variants
    scan        locate figures in a corpus, across the include graph
    describe    deterministic (non-AI) description skeletons
    catalog     content-addressed store + Markdown worklogs
    apply       write accessibility markup back into .tex
    mathspeech  LaTeX -> MathML -> spoken formula alt text
    build       inject, materialise, compile
    check       source lint, log scan, PDF structure and speech assertions
    agent       machine-readable task API for LLM agents
    tui         the interactive runner, drawn with Rich over stdlib key input

Stage names describe what exists. `check` does not shell out to veraPDF, the
build is not parallel, and the runner is not built on Textual -- each of those
was in this list once and none of them was ever true.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
