#!/usr/bin/env python3
"""Generate standalone Vitis HLS projects for manual optimization.

For every program file that lives in the configured WORK_DIR (or a user supplied
directory), this script builds a small project directory that mirrors the setup
from ``flow/tools/csynth.py``: it copies the source into the project folder and
drops a matching ``vitis.tcl`` script. Each generated TCL file uses ``set_top
top`` so the entry function is uniform across all designs. If a program's
kernel function is not already named ``top``, the script attempts to rename it
in the copied source.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import dotenv  # type: ignore

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flow.tools.csynth import make_vitis_tcl

dotenv.load_dotenv(BASE_DIR / ".env", override=True)
WORK_DIR = os.getenv("WORK_DIR")
DEFAULT_TOP_NAME = "top"

FUNC_DEF_RE = re.compile(
    r'^\s*(?:extern\s+"C"\s+)?(?:static\s+)?(?:inline\s+)?'
    r'(?:constexpr\s+)?(?:volatile\s+)?'
    r'[A-Za-z_][\w:<>,\s\*\&]*?\s+(?P<name>[A-Za-z_]\w*)\s*\(',
    re.MULTILINE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one Vitis HLS project per input program."
    )
    parser.add_argument(
        "--programs-dir",
        type=Path,
        default=None,
        help="Directory containing *.cpp programs (defaults to WORK_DIR/summary/programs, "
        "then WORK_DIR, then runs/summary/programs).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where project folders will be written "
        "(defaults to WORK_DIR/manual_hls_projects).",
    )
    parser.add_argument(
        "--extension",
        type=str,
        default=".cpp",
        help="Program file extension to search for (default: .cpp).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing project folders instead of skipping them.",
    )
    return parser.parse_args()


def resolve_programs_dir(explicit_dir: Optional[Path]) -> Path:
    candidates: List[Path] = []
    if explicit_dir is not None:
        candidates.append(explicit_dir)
    if WORK_DIR:
        work_path = Path(WORK_DIR)
        candidates.append(work_path / "summary" / "programs")
        candidates.append(work_path)
    candidates.append(BASE_DIR / "runs" / "summary" / "programs")

    for candidate in candidates:
        if candidate and candidate.exists() and candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "Unable to locate a directory with programs. Specify --programs-dir explicitly."
    )


def resolve_output_dir(explicit_dir: Optional[Path]) -> Path:
    if explicit_dir is not None:
        return explicit_dir
    if WORK_DIR:
        return Path(WORK_DIR) / "manual_hls_projects"
    return BASE_DIR / "tmp" / "manual_hls_projects"


def ensure_top_function(
    source: str, target_name: str = DEFAULT_TOP_NAME
) -> Tuple[str, Optional[str], bool]:
    search_start = source.find("#pragma ACCEL kernel")
    if search_start == -1:
        search_start = 0
    match = FUNC_DEF_RE.search(source, search_start)
    if not match:
        return source, None, False
    current_name = match.group("name")
    if current_name == target_name:
        return source, current_name, False
    start, end = match.span("name")
    updated = f"{source[:start]}{target_name}{source[end:]}"
    return updated, current_name, True


def create_project_for_program(
    program_path: Path,
    output_root: Path,
    overwrite: bool,
    top_name: str = DEFAULT_TOP_NAME,
) -> bool:
    project_dir = output_root / program_path.stem
    if project_dir.exists():
        if not overwrite:
            print(
                f"[skip] {program_path.name}: {project_dir} already exists. "
                "Use --overwrite to rebuild."
            )
            return False
        shutil.rmtree(project_dir)

    project_dir.mkdir(parents=True, exist_ok=True)
    dest_source_path = project_dir / program_path.name

    content = program_path.read_text(encoding="utf-8")
    updated_content, previous_name, renamed = ensure_top_function(content, top_name)
    if previous_name is None:
        print(
            f"[warn] {program_path.name}: could not find a kernel function to rename."
        )
    elif renamed:
        print(
            f"[info] {program_path.name}: renamed kernel {previous_name} -> {top_name}."
        )

    dest_source_path.write_text(updated_content, encoding="utf-8")
    vitis_tcl = make_vitis_tcl(top_name, [program_path.name])
    (project_dir / "vitis.tcl").write_text(vitis_tcl, encoding="utf-8")

    print(f"[ok] Created project at {project_dir}")
    return True


def main() -> int:
    args = parse_args()
    programs_dir = resolve_programs_dir(args.programs_dir.resolve() if args.programs_dir else None)
    output_dir = resolve_output_dir(args.output_dir.resolve() if args.output_dir else None)
    output_dir.mkdir(parents=True, exist_ok=True)

    program_files = sorted(
        path
        for path in programs_dir.iterdir()
        if path.is_file() and path.suffix == args.extension
    )
    if not program_files:
        print(f"No '{args.extension}' programs found in {programs_dir}. Nothing to do.")
        return 0

    created = 0
    for program_path in program_files:
        if create_project_for_program(program_path, output_dir, args.overwrite):
            created += 1

    print(
        f"\nGenerated {created} project(s) in {output_dir} "
        f"from {len(program_files)} program(s) under {programs_dir}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
