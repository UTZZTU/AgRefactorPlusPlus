"""Provider-neutral prompt construction for repair and optimization."""

from .candidate_repair import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
    build_candidate_csynth_repair_prompt,
    build_candidate_public_csim_repair_prompt,
)
from .test_source_isolation import (
    HiddenTestSourceIsolationError,
    assert_hidden_test_sources_absent,
)
from .layered import (
    LayeredPrompt,
    LayeredPromptRequest,
    ModificationScope,
    PromptArtifact,
    PromptOutputContract,
    PromptPurpose,
    SharedLayeredPromptBuilder,
)

__all__ = [
    "CandidateRepairPromptInputs",
    "HiddenTestSourceIsolationError",
    "LayeredPrompt",
    "LayeredPromptRequest",
    "ModificationScope",
    "PromptArtifact",
    "PromptOutputContract",
    "PromptPurpose",
    "SharedLayeredPromptBuilder",
    "assert_hidden_test_sources_absent",
    "build_candidate_compile_repair_prompt",
    "build_candidate_csynth_repair_prompt",
    "build_candidate_public_csim_repair_prompt",
]
