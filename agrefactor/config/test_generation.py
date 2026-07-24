"""Typed Testbench-generation profile selection."""

from __future__ import annotations

from enum import Enum


DEFAULT_PUBLIC_COVERAGE_ROUNDS = 3
DEFAULT_TEST_GENERATION_TRAJECTORIES = 3
DEFAULT_HIDDEN_COVERAGE_ROUNDS = 6


class TestGenerationProfile(str, Enum):
    """User-facing Testbench-generation strategy."""

    LIGHTWEIGHT = "lightweight"
    COVERAGE_ENHANCED = "coverage-enhanced"


def resolve_test_generation_profile(
    value: str | TestGenerationProfile | None,
) -> TestGenerationProfile:
    """Normalize an optional profile value with a lightweight default."""

    if value is None:
        return TestGenerationProfile.LIGHTWEIGHT
    if isinstance(value, TestGenerationProfile):
        return value
    if not isinstance(value, str):
        raise TypeError(
            "test generation profile must be a string, "
            "TestGenerationProfile, or None"
        )
    cleaned = value.strip()
    try:
        return TestGenerationProfile(cleaned)
    except ValueError as exc:
        choices = ", ".join(item.value for item in TestGenerationProfile)
        raise ValueError(
            "unsupported test generation profile "
            f"{value!r}; expected one of: {choices}"
        ) from exc
