"""Use-case orchestration (ConfigIngestionService, Clock, UnitOfWork, ...).

No FastAPI/SQLAlchemy types. See architecture.md Section 2 and Section 4.

Day 5A adds the first use case: ``ConfigIngestionService`` orchestrates one
``UnitOfWork``-scoped transaction across the existing Device/
ConfigurationSnapshot/ConfigurationPolicy/Incident repositories, adapter
normalization, policy evaluation, and incident upsert — see CLAUDE.md
"Current Phase".
"""

from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.errors import ConfigurationParseError
from meta_rne.application.models import ConfigIngestionResult, IngestConfigurationCommand
from meta_rne.application.snapshot_id import default_snapshot_id_factory

__all__ = [
    "ConfigIngestionResult",
    "ConfigIngestionService",
    "ConfigurationParseError",
    "IngestConfigurationCommand",
    "default_snapshot_id_factory",
]
