"""External RAG project catalog and Vision project identity bindings."""

from .contracts import (
    ExternalProjectCatalogRecord,
    ExternalProjectSyncReport,
    ProjectExternalBindingRecord,
    RagTargetRecord,
)
from .repository import ExternalProjectRegistryError, PostgresExternalProjectRegistry
from .service import ExternalProjectRegistryService

__all__ = [
    "ExternalProjectCatalogRecord",
    "ExternalProjectRegistryError",
    "ExternalProjectRegistryService",
    "ExternalProjectSyncReport",
    "PostgresExternalProjectRegistry",
    "ProjectExternalBindingRecord",
    "RagTargetRecord",
]
