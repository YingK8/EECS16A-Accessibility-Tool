# LaTeX Accessibility Automation: TEXINPUTS Setup

## Why use TEXINPUTS?
This project now uses a robust, automation-friendly approach for custom LaTeX packages. All custom style files (like `accessibility_format.sty`) are referenced by name only:

```
\usepackage{accessibility_format}
\usepackage{hyperref}
```

This works in every subdirectory if you set the environment variable `TEXINPUTS` to include the project root. This eliminates fragile relative paths and makes automation and manual compilation reliable.

---

## How to set TEXINPUTS

**For bash/zsh (macOS/Linux):**

In the question bank root directory, run:
```
export TEXINPUTS="$(pwd):"
```
Or, for a specific project path:
```
export TEXINPUTS="/path/to/questionBank:"
```
Verify the TEXINPUT is correct by:
```
echo $TEXINPUTS
```

**For one-off commands:**
```
TEXINPUTS="/path/to/questionBank:" pdflatex myfile.tex
```

**For Makefiles:**
Add this to your Makefile:
```
export TEXINPUTS := /path/to/questionBank:
```

Note, that this is temporary for each bash session. to make this permanent, you can put the export command in ~/.bashrc:
```
echo 'export TEXINPUTS="/path/to/questionBank:"' >> ~/.bashrc
```

---

## Example: Compile from any subdirectory
```
cd /path/to/questionBank
export TEXINPUTS="$(pwd):"
cd sp26/hw/9
pdflatex sol9.tex
```

---

## Notes
- Always use a trailing colon (`:`) to preserve system package search paths.
- export is temporary unless you put it in ~/.bashrc
- You can now use `\usepackage{accessibility_format}
\usepackage{hyperref}` everywhere—no more relative paths.
- This approach is robust for both automation and manual builds, regardless of directory depth or preamble sharing.
