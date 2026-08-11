from __future__ import annotations

from .catalog_core import GenerationCatalogCoreMixin
from .catalog_ids import GenerationCatalogIdsMixin
from .catalog_models import GenerationCatalogModelsMixin
from .catalog_status import GenerationCatalogStatusMixin


class GenerationCatalogMixin(
    GenerationCatalogModelsMixin,
    GenerationCatalogStatusMixin,
    GenerationCatalogIdsMixin,
    GenerationCatalogCoreMixin,
):
    """Combined catalog/status policy surface used by GenerationRouter."""
