"""Read-only Containerlab/FRR live operational-state adapter (NPE-1C3A).

Device-facing, inbound-only: raw command output in, normalized
``FabricOperationalState`` out. Depends on ``meta_rne.domain`` only; the real
subprocess runner lives at the outermost boundary (``scripts/``), never here.
"""

from meta_rne.adapters.containerlab_frr.collector import (
    CommandNotAllowedError,
    CommandResult,
    ContainerlabFrrCollector,
    NodeCommandRunner,
)
from meta_rne.adapters.containerlab_frr.parsers import OutputParseError

__all__ = [
    "CommandNotAllowedError",
    "CommandResult",
    "ContainerlabFrrCollector",
    "NodeCommandRunner",
    "OutputParseError",
]
