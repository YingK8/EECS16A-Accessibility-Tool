import os
import sys


def main():
    print(
        """
╔════════════════════════════════════════════════════════════════╗
║        EECS 16A Accessibility Tool                            ║
╚════════════════════════════════════════════════════════════════╝

Reformats LaTeX question bank files to meet WCAG 2.1 standards.

Current features:
  • PDF tagging: Semantic headings (title→H1, questions→H2, etc.)
  • Document title metadata for screen readers

Note: This tool is a work in progress and focuses on PDF tagging.
        """
    )

    print("\nSelect updater:")
    print("  0) Style + Assignment files")
    print("  1) Exam files (.tex)")
    print("  2) Assignment files (.tex)")
    print("  3) Style files (.sty)")
    choice = input("Enter choice (0-3): ").strip()

    updater_map = {
        "1": ("exam_macro_updater", "Exam"),
        "2": ("assignment_macro_updater", "Assignment"),
        "3": ("style_macro_updater", "Style"),
    }

    if choice not in ("0", "1", "2", "3"):
        print("Invalid choice.")
        sys.exit(1)

    if choice == "0":
        modules = [
            ("style_macro_updater", "Style"),
            ("assignment_macro_updater", "Assignment"),
        ]
    else:
        modules = [(updater_map[choice][0], updater_map[choice][1])]

    root_dir = input("\nTarget directory path: ").strip()
    if not os.path.isdir(root_dir):
        print(f"Error: Directory not found: {root_dir}")
        sys.exit(1)

    backup_input = input("Create backups? (y/n, default: y): ").strip().lower()
    create_backup = backup_input != "n"

    log_input = input("Create log file? (y/n, default: y): ").strip().lower()
    create_log = log_input != "n"

    try:
        for module_name, label in modules:
            module = __import__(module_name)
            module.main(root_dir, create_backup, create_log)
            print(f"✓ {label} files updated.")
        print("\nAll updates completed successfully.")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
