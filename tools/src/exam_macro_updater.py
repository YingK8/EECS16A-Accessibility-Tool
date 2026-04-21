#!/usr/bin/env python3


import os
import re
from common import (
    ensure_accessibility_package,
    ensure_pdf_tagging_preamble,
    replace_first_matching_pattern,
)
from patterns import SOL_PATTERNS, REQUIRED_RENEW, REQUIRED_NEW, REQUIRED_DEF


QITEM_DEF_PATTERN = re.compile(
    r"^\s*\\newcommand\{\\qitem\}\{\\qpart\\item\}\s*$", re.MULTILINE
)
INLINE_QITEM_PATTERN = re.compile(
    r"^([ \t]*)\\qitem(?![a-zA-Z])(?:[ \t]*\n)?[ \t]*(?!\{)((?:(?!\\qitem(?![a-zA-Z])|\\sol(?![a-zA-Z])|\\solution(?![a-zA-Z])|\\end\{qns\}|\\end\{qun\}).)*)",
    re.DOTALL | re.MULTILINE,
)
SOLUTION_SEPARATOR_PATTERN = re.compile(
    r"(?<!\n)\n[ \t]*(?=\\sol(?![a-zA-Z])|\\solution(?![a-zA-Z]))"
)
INLINE_SOL_SINGLELINE_PATTERN = re.compile(
    r"^([ \t]*)\\(sol|solution)(?![a-zA-Z])[ \t]+(?!\{)([^%\n]*[^%\s\n][^%\n]*)([ \t]*(?:%.*)?)$",
    re.MULTILINE,
)
TITLE_DEF_PATTERN = re.compile(r"^\s*\\def\\title\{([^{}]+)\}\s*$", re.MULTILINE)
TITLE_CMD_PATTERN = re.compile(r"^\s*\\title\{([^{}]+)\}\s*$", re.MULTILINE)
ACCESSIBILITY_SET_TITLE_PATTERN = re.compile(
    r"^\s*\\AccessibilitySetDocumentTitle\{[^\n]*\}\s*$", re.MULTILINE
)
ACCESSIBILITY_APPLY_METADATA_PATTERN = re.compile(
    r"^\s*\\AtBeginDocument\{\\AccessibilityApplyDocumentMetadata\}\s*$", re.MULTILINE
)
BEGIN_DOCUMENT_PATTERN = re.compile(r"^\s*\\begin\{document\}\s*$", re.MULTILINE)
USEPACKAGE_PATTERN = re.compile(
    r"^\s*\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{[^}]+\}\s*$", re.MULTILINE
)
INPUT_PATTERN = re.compile(r"^\s*\\input\{[^}]+\}\s*$", re.MULTILINE)


def should_process_file(filepath: str) -> bool:
    return filepath.endswith(".tex") and not filepath.endswith(".sty")


def _infer_document_title(content: str, filepath: str) -> str:
    title_match = TITLE_DEF_PATTERN.search(content)
    if title_match:
        return title_match.group(1).strip()
    title_match = TITLE_CMD_PATTERN.search(content)
    if title_match:
        return title_match.group(1).strip()
    fallback = os.path.splitext(os.path.basename(filepath))[0]
    return fallback.replace("_", " ").strip() or "EECS 16A Assignment"


def ensure_accessibility_document_title(content: str, filepath: str):
    if re.search(r"^\s*\\documentclass", content, re.MULTILINE) is None:
        return content
    if not ACCESSIBILITY_SET_TITLE_PATTERN.search(content):
        title_value = _infer_document_title(content, filepath)
        set_title_line = f"\\AccessibilitySetDocumentTitle{{{title_value}}}"
        title_match = TITLE_DEF_PATTERN.search(content) or TITLE_CMD_PATTERN.search(
            content
        )
        if title_match:
            insert_pos = title_match.end()
            content = (
                content[:insert_pos] + "\n" + set_title_line + content[insert_pos:]
            )
        else:
            package_matches = list(USEPACKAGE_PATTERN.finditer(content))
            if package_matches:
                insert_pos = package_matches[-1].end()
                content = (
                    content[:insert_pos] + "\n" + set_title_line + content[insert_pos:]
                )
            else:
                docclass_match = re.search(
                    r"^\s*\\documentclass.*$", content, re.MULTILINE
                )
                if docclass_match:
                    insert_pos = docclass_match.end()
                    content = (
                        content[:insert_pos]
                        + "\n"
                        + set_title_line
                        + content[insert_pos:]
                    )
                else:
                    content = set_title_line + "\n" + content
    if not ACCESSIBILITY_APPLY_METADATA_PATTERN.search(content):
        apply_line = "\\AtBeginDocument{\\AccessibilityApplyDocumentMetadata}"
        begin_doc_match = BEGIN_DOCUMENT_PATTERN.search(content)
        if begin_doc_match:
            insert_pos = begin_doc_match.start()
        else:
            set_title_match = ACCESSIBILITY_SET_TITLE_PATTERN.search(content)
            if set_title_match:
                insert_pos = set_title_match.end()
            else:
                package_matches = list(USEPACKAGE_PATTERN.finditer(content))
                if package_matches:
                    insert_pos = package_matches[-1].end()
                else:
                    input_match = INPUT_PATTERN.search(content)
                    if input_match:
                        insert_pos = input_match.start()
                    else:
                        docclass_match = re.search(
                            r"^\s*\\documentclass.*$", content, re.MULTILINE
                        )
                        insert_pos = docclass_match.end() if docclass_match else 0
        insertion = "\n" + apply_line + "\n"
        content = content[:insert_pos] + insertion + content[insert_pos:]
    return content


def process_file(filepath: str, root_dir: str) -> None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return
    content = QITEM_DEF_PATTERN.sub(r"\\long\\def\\qitem#1{\\qpart\\item #1}", content)

    def qitem_repl(m):
        leading_ws = m.group(1) or ""
        content_body = m.group(2)
        content_lines = content_body.splitlines()
        while content_lines and content_lines[0].strip() == "":
            content_lines.pop(0)
        while content_lines and content_lines[-1].strip() == "":
            content_lines.pop()
        indented_body = "\n".join(
            "    " + line if line.strip() != "" else "" for line in content_lines
        )
        indented_body = re.sub(r"[ \t\r\n]+\Z", "", indented_body)
        return f"{leading_ws}\\qitem{{\n{indented_body}\n}}\n\n"

    content = INLINE_QITEM_PATTERN.sub(qitem_repl, content)

    def inline_sol_repl(m):
        leading_ws = m.group(1) or ""
        cmd = m.group(2)
        body = m.group(3).strip()
        trailing_comment = m.group(4) or ""
        if trailing_comment:
            trailing_comment = " " + trailing_comment.lstrip()
        return f"{leading_ws}\\{cmd}{{{body}}}{trailing_comment}"

    content = INLINE_SOL_SINGLELINE_PATTERN.sub(inline_sol_repl, content)
    content = SOLUTION_SEPARATOR_PATTERN.sub("\n\n", content)
    if re.search(r"^\s*\\documentclass", content, re.MULTILINE):
        content, _ = ensure_accessibility_package(
            content,
            filepath=filepath,
            root_dir=root_dir,
            insert_after_documentclass=True,
        )
    content, _ = replace_first_matching_pattern(
        content,
        SOL_PATTERNS,
        REQUIRED_RENEW["sol"],
        REQUIRED_NEW["sol"],
        REQUIRED_DEF["sol"],
    )
    content, _ = ensure_pdf_tagging_preamble(content)
    content = ensure_accessibility_document_title(content, filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def collect_target_files(root_dir: str) -> list[str]:
    targets = set()
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if should_process_file(filepath) and not filepath.endswith(".bak"):
                targets.add(os.path.normpath(filepath))
    return sorted(targets)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python exam_macro_updater.py /path/to/target/directory")
        sys.exit(1)
    root_dir = os.path.abspath(sys.argv[1])
    for filepath in collect_target_files(root_dir):
        process_file(filepath, root_dir)
