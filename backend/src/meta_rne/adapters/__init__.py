"""Vendor configuration adapters (Cisco, Arista), one module per vendor.

Cisco IOS-XE is implemented (``cisco.py``). Arista is not implemented yet
(prohibited until a later day — see CLAUDE.md "Current Phase"). See
architecture.md Section 5.
"""

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry

__all__ = ["AdapterRegistry", "CiscoAdapter"]
