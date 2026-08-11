from __future__ import annotations

from .execution_backendai import GenerationBackendAIExecutionMixin
from .execution_cloud import GenerationCloudExecutionMixin
from .execution_common import GenerationExecutionCommonMixin


class GenerationExecutionMixin(
    GenerationBackendAIExecutionMixin,
    GenerationCloudExecutionMixin,
    GenerationExecutionCommonMixin,
):
    """Combined provider-execution surface used by GenerationRouter."""
