"""Configuration schemas and target descriptions."""

from .target import (
    DEFAULT_TARGET_PROFILE_NAME,
    TargetProfile,
    TargetResourceLimits,
    default_target_profile,
    resolve_target_profile,
)
from .target_profiles import (
    available_target_profile_names,
    target_profile_config_dir,
)
from .task import RunMode, TaskSpec
from .test_source import (
    TestSourceKind,
    TestSourceProvenance,
    TestSourceSpec,
    resolve_test_source,
)
from .test_suite import EvaluationSplit, TestSuiteSpec

__all__ = [
    "DEFAULT_TARGET_PROFILE_NAME",
    "EvaluationSplit",
    "RunMode",
    "TargetProfile",
    "TargetResourceLimits",
    "TaskSpec",
    "TestSourceKind",
    "TestSourceProvenance",
    "TestSourceSpec",
    "TestSuiteSpec",
    "available_target_profile_names",
    "default_target_profile",
    "resolve_target_profile",
    "resolve_test_source",
    "target_profile_config_dir",
]
