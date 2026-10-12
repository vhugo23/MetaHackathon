"""Fixed Lab 1 identity constants (NPE-1D1B).

A neutral, dependency-free module so the collector, the detector, device
registration and the read API share one definition without any of them
importing another's layer. Pure constants: no framework, I/O, or topology.
"""

LAB1_LAB_NAME = "meta-rne-bgp"

# Collector node -> registered platform device ID. Hosts are not registered
# and are deliberately absent.
LAB1_NODE_DEVICE_IDS: dict[str, str] = {
    "spine-1": "lab1-spine-1",
    "spine-2": "lab1-spine-2",
    "leaf-1": "lab1-leaf-1",
    "leaf-2": "lab1-leaf-2",
}
