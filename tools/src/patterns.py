# Macro patterns (line-based to safely handle legacy one-line definitions)
import re


TITLE_PATTERNS = [
    re.compile(r"^\s*\\renewcommand\{\\title\}\[1\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\newcommand\{\\title\}\[1\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\def\\title#1\{.*\}\s*$", re.MULTILINE),
]

ACCESSIBLE_TITLE_STORAGE_PATTERN = re.compile(
    r"^\s*\\newcommand\{\\accessibletitle\}\{\}\s*$", re.MULTILINE
)

TITLE_H1_RENDER_PATTERNS = [
    re.compile(
        r"\{\\dunhb\s+\\hfill\s+\\dunhbb\s+\\AccessibilityHeadingOne\{\\title\}\s+\\par\}",
        re.MULTILINE,
    ),
    re.compile(
        r"\{\\dunhb\s+\\hfill\s+\\dunhbb\s+\\title\s+\\par\}",
        re.MULTILINE,
    ),
]

TITLE_RENDER_CONTEXT_PATTERNS = TITLE_H1_RENDER_PATTERNS + [
    re.compile(
        r"\{\\dunhb\s+\\hfill\s+\\dunhbb\s+\\AccessibilityHeadingOne\{\\accessibletitle\}\s+\\par\}",
        re.MULTILINE,
    )
]

QNS_PATTERNS = [
    re.compile(r"^\s*\\renewcommand\{\\qns\}\[1\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\newcommand\{\\qns\}\[1\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\def\\qns#1\{.*\}\s*$", re.MULTILINE),
]
Q_PATTERNS = [
    re.compile(r"^\s*\\renewcommand\{\\q\}\[2\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\newcommand\{\\q\}\[2\]\{.*\}\s*$", re.MULTILINE),
    re.compile(r"^\s*\\def\\q#1#2\{.*\}\s*$", re.MULTILINE),
]

# AccessibilityHeading* patterns
ACC_HEADINGS = [
    (
        "One",
        r"\\newcommand\{\\AccessibilityHeadingOne\}\[1\]\{.*?\}",
        r"\\newcommand{\\AccessibilityHeadingOne}[1]{#1}",
    ),
    (
        "Two",
        r"\\newcommand\{\\AccessibilityHeadingTwo\}\[1\]\{.*?\}",
        r"\\newcommand{\\AccessibilityHeadingTwo}[1]{#1}",
    ),
    (
        "Three",
        r"\\newcommand\{\\AccessibilityHeadingThree\}\[1\]\{.*?\}",
        r"\\newcommand{\\AccessibilityHeadingThree}[1]{#1}",
    ),
    (
        "Four",
        r"\\newcommand\{\\AccessibilityHeadingFour\}\[1\]\{.*?\}",
        r"\\newcommand{\\AccessibilityHeadingFour}[1]{#1}",
    ),
]

# Required macro definitions
REQUIRED_RENEW = {
    "title": r"\renewcommand{\title}[1]{\gdef\accessibletitle{#1}\AccessibilitySetDocumentTitle{#1}}",
    "qns": r"\renewcommand{\qns}[1]{\bf\item \AccessibilityHeadingTwo{#1}}",
    "q": r"\renewcommand{\q}[2]{\bf\item (#1 pts.)\quad \AccessibilityHeadingThree{#2}}",
    "sol": r"\renewcommand{\sol}[1]{{\color{blue} \AccessibilityHeadingFour{\textbf{Solution:}} #1}}",
}
REQUIRED_NEW = {
    "title": r"\newcommand{\title}[1]{\gdef\accessibletitle{#1}\AccessibilitySetDocumentTitle{#1}}",
    "qns": r"\newcommand{\qns}[1]{\bf\item \AccessibilityHeadingTwo{#1}}",
    "q": r"\newcommand{\q}[2]{\bf\item (#1 pts.)\quad \AccessibilityHeadingThree{#2}}",
    "sol": r"\newcommand{\sol}[1]{{\color{blue} \AccessibilityHeadingFour{\textbf{Solution:}} #1}}",
}
REQUIRED_DEF = {
    "title": r"\def\title#1{\gdef\accessibletitle{#1}\AccessibilitySetDocumentTitle{#1}}",
    "qns": r"\def\qns#1{{\bf\item \AccessibilityHeadingTwo{#1}}}",
    "q": r"\def\q#1#2{{\bf\item (#1 pts.)\quad \AccessibilityHeadingThree{#2}}}",
    "sol": r"\def\sol#1{{\color{blue} \AccessibilityHeadingFour{\textbf{Solution:}} #1}}",
}

REQUIRED_ACCESSIBLE_TITLE_STORAGE = r"\newcommand{\accessibletitle}{}"
REQUIRED_TITLE_SETTER = REQUIRED_RENEW["title"]
REQUIRED_TITLE_H1_RENDER = (
    r"{\dunhb \hfill \dunhbb \AccessibilityHeadingOne{\accessibletitle} \par}"
)

# Keep SOL replacement line-based so only the macro definition line is touched.
# This prevents accidental capture/removal of following lines such as \reader or \input.
SOL_PATTERNS = [
    re.compile(
        r"^\s*\\renewcommand\{\\sol\}\[1\]\{.*\}\s*(?:%.*)?$",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*\\newcommand\{\\sol\}\[1\]\{.*\}\s*(?:%.*)?$",
        re.MULTILINE,
    ),
    re.compile(r"^\s*\\def\\sol#1\{.*\}\s*(?:%.*)?$", re.MULTILINE),
]
