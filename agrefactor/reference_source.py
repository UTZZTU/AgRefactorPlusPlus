"""Prepare Original source for use as an in-process reference oracle."""

from __future__ import annotations

_REFERENCE_ENTRY = "agrefactor_original_program_main"


def isolate_reference_program_entry(source_code: str) -> str:
    """Rename an Original program entry without changing the selected top."""

    if not isinstance(source_code, str):
        raise TypeError("source_code must be a string")
    if not source_code.strip():
        raise ValueError("source_code must not be empty")
    return (
        f"#define main {_REFERENCE_ENTRY}\n"
        + source_code.rstrip()
        + "\n#undef main\n"
    )
