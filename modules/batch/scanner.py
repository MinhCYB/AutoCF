"""
Batch folder scanner — detects problem files, solutions, and tests
in a structured directory of competitive programming problems.

Expected input structure:
    problems/
    ├── bai1/
    │   └── de.pdf
    ├── bai2/
    │   ├── bai2.docx
    │   ├── solution.cpp
    │   └── tests/
    │       ├── 1
    │       ├── 1.a
    │       ├── 2
    │       └── 2.a
"""

from pathlib import Path

from modules.parser.file_loader import SUPPORTED_EXTENSIONS

# Extensions recognized as solution source code
SOLUTION_EXTENSIONS = {".cpp", ".c", ".py", ".java", ".pas", ".rs", ".go"}
SOLUTION_STEMS = {"solution", "sol", "main"}


def scan_problems_dir(
    root: Path,
    level: str,
    contest_name: str,
    start_index: int,
) -> list[dict]:
    """
    Scan a root directory for problem subfolders.

    Each subfolder is treated as one problem. Generates polygon_name
    using the pattern: "{level} - {contest_name} - {index:02d}"

    Args:
        root: Root directory containing problem subfolders.
        level: Level prefix (e.g. "lv1", "lv2").
        contest_name: Contest/topic name (e.g. "array", "dp").
        start_index: Starting index for numbering.

    Returns:
        List of dicts with scan results for each problem folder.
    """
    if not root.is_dir():
        raise ValueError(f"Thư mục không tồn tại: {root}")

    results = []
    folders = sorted(
        [f for f in root.iterdir() if f.is_dir() and not f.name.startswith(".")],
        key=lambda f: f.name,
    )

    for i, folder in enumerate(folders):
        index = start_index + i
        polygon_name = f"{level} - {contest_name} - {index:02d}"

        # --- Detect problem files ---
        problem_files = [
            f
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        # --- Detect solution file ---
        solution_path = ""
        for f in folder.iterdir():
            if not f.is_file():
                continue
            if (
                f.stem.lower() in SOLUTION_STEMS
                and f.suffix.lower() in SOLUTION_EXTENSIONS
            ):
                solution_path = str(f)
                break

        # If no named solution, look for any single source file
        if not solution_path:
            code_files = [
                f
                for f in folder.iterdir()
                if f.is_file()
                and f.suffix.lower() in SOLUTION_EXTENSIONS
                and f.stem.lower() not in ("checker", "validator", "generator")
            ]
            if len(code_files) == 1:
                solution_path = str(code_files[0])

        # --- Detect tests directory ---
        tests_dir = ""
        tests_path = folder / "tests"
        if tests_path.is_dir():
            tests_dir = str(tests_path)

        # --- Determine status ---
        if len(problem_files) == 0:
            status = "error"
            warning = "Không tìm thấy file đề"
        elif len(problem_files) > 1:
            status = "warning"
            warning = f"Tìm thấy {len(problem_files)} file đề, chọn 1 file"
        else:
            status = "ok"
            warning = ""

        results.append(
            {
                "index": i,
                "folder": folder.name,
                "folder_path": str(folder),
                "polygon_name": polygon_name,
                "problem_files": [
                    {
                        "name": f.name,
                        "path": str(f),
                        "ext": f.suffix.lower().lstrip("."),
                    }
                    for f in sorted(problem_files, key=lambda f: f.name)
                ],
                "selected_file": (
                    str(problem_files[0]) if len(problem_files) == 1 else ""
                ),
                "solution_path": solution_path,
                "tests_dir": tests_dir,
                "status": status,
                "warning": warning,
            }
        )

    return results
