#!/usr/bin/env python3
"""Offline Lab 1 contract validator (NPE-1B2, ADR-0003).

Statically checks the committed ``lab/`` definition against the contract in
docs/adr/0003-network-lab-and-live-state-collection.md. Standard-library
only; never invokes Docker or Containerlab, never touches the network, never
needs root. It proves the *definition* is internally consistent — it does
**not** prove the lab runs (no BGP session, reachability, or ECMP is
exercised here; that needs the live deployment gate).

The topology reader is deliberately narrow: it understands only the YAML
subset used by ``lab/topology.clab.yml`` (block mappings, block sequences,
quoted/unquoted scalars, single-line flow sequences) and is not a general
YAML parser.

Usage:
    python scripts/lab_validate.py [--lab-dir lab]

Exit status: 0 when the contract holds, 1 on any contract failure.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LAB_NAME = "meta-rne-bgp"
ROUTER_IMAGE = "quay.io/frrouting/frr:10.6.1"
HOST_IMAGE = "alpine:3.24.2"
HOST_CMD = "tail -f /dev/null"

ROUTERS = ("spine-1", "spine-2", "leaf-1", "leaf-2")
HOSTS = ("host-1", "host-2")

EXPECTED_LINKS = frozenset(
    frozenset(pair)
    for pair in (
        ("leaf-1:eth1", "spine-1:eth1"),
        ("leaf-1:eth2", "spine-2:eth1"),
        ("leaf-2:eth1", "spine-1:eth2"),
        ("leaf-2:eth2", "spine-2:eth2"),
        ("host-1:eth1", "leaf-1:eth3"),
        ("host-2:eth1", "leaf-2:eth3"),
    )
)

EXPECTED_HOST_EXEC = {
    "host-1": [
        "ip link set eth1 up",
        "ip addr add 10.1.1.10/24 dev eth1",
        "ip route replace default via 10.1.1.1 dev eth1",
    ],
    "host-2": [
        "ip link set eth1 up",
        "ip addr add 10.1.2.10/24 dev eth1",
        "ip route replace default via 10.1.2.1 dev eth1",
    ],
}


@dataclass(frozen=True)
class RouterSpec:
    asn: int
    loopback: str
    interfaces: dict[str, str]
    neighbors: dict[str, int]
    networks: frozenset[str]
    maximum_paths: int | None  # None = not required


ROUTER_SPECS: dict[str, RouterSpec] = {
    "spine-1": RouterSpec(
        asn=65000,
        loopback="10.0.0.1/32",
        interfaces={"eth1": "10.255.0.0/31", "eth2": "10.255.0.4/31"},
        neighbors={"10.255.0.1": 65101, "10.255.0.5": 65102},
        networks=frozenset({"10.0.0.1/32"}),
        maximum_paths=None,
    ),
    "spine-2": RouterSpec(
        asn=65000,
        loopback="10.0.0.2/32",
        interfaces={"eth1": "10.255.0.2/31", "eth2": "10.255.0.6/31"},
        neighbors={"10.255.0.3": 65101, "10.255.0.7": 65102},
        networks=frozenset({"10.0.0.2/32"}),
        maximum_paths=None,
    ),
    "leaf-1": RouterSpec(
        asn=65101,
        loopback="10.0.0.11/32",
        interfaces={
            "eth1": "10.255.0.1/31",
            "eth2": "10.255.0.3/31",
            "eth3": "10.1.1.1/24",
        },
        neighbors={"10.255.0.0": 65000, "10.255.0.2": 65000},
        networks=frozenset({"10.0.0.11/32", "10.1.1.0/24"}),
        maximum_paths=2,
    ),
    "leaf-2": RouterSpec(
        asn=65102,
        loopback="10.0.0.12/32",
        interfaces={
            "eth1": "10.255.0.5/31",
            "eth2": "10.255.0.7/31",
            "eth3": "10.1.2.1/24",
        },
        neighbors={"10.255.0.4": 65000, "10.255.0.6": 65000},
        networks=frozenset({"10.0.0.12/32", "10.1.2.0/24"}),
        maximum_paths=2,
    ),
}

# Forbidden anywhere in an FRR config (case-insensitive, whole-word where
# a bare word could collide with an ordinary token).
_FORBIDDEN_CONFIG_PATTERNS = (
    (re.compile(r"\bospf6?\b|\bospf6d\b", re.IGNORECASE), "OSPF"),
    (re.compile(r"\bisis\b|\bis-is\b|\bfabricd\b", re.IGNORECASE), "IS-IS"),
    (re.compile(r"\bmpls\b|\bldp\b", re.IGNORECASE), "MPLS/LDP"),
    (re.compile(r"\bgre\b", re.IGNORECASE), "GRE"),
    (re.compile(r"\bipip\b|\bip-in-ip\b|\bipinip\b", re.IGNORECASE), "IPIP"),
)

_ENABLED_DAEMON_ALLOWLIST = frozenset({"bgpd", "vtysh_enable"})


# --------------------------------------------------------------------------
# Narrow YAML-subset reader
# --------------------------------------------------------------------------

_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+(.*))?$")


class TopologyParseError(ValueError):
    """The topology file uses syntax outside the supported subset."""


def _strip_comment(line: str) -> str:
    if '"' in line or "'" in line:
        return line.rstrip()
    return re.sub(r"\s+#.*$", "", line).rstrip()


def _tokenize(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise TopologyParseError("tab indentation is not supported")
        lines.append((len(raw) - len(raw.lstrip(" ")), _strip_comment(stripped)))
    return lines


def _scalar(value: str) -> object:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_block(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[object, int]:
    if lines[index][1].startswith("- ") or lines[index][1] == "-":
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    while index < len(lines) and lines[index][0] == indent:
        match = _KEY_RE.match(lines[index][1])
        if match is None:
            raise TopologyParseError(f"unsupported line: {lines[index][1]!r}")
        key, value = match.group(1), match.group(2)
        index += 1
        if value:
            result[key] = _scalar(value)
        elif index < len(lines) and lines[index][0] > indent:
            result[key], index = _parse_block(lines, index, lines[index][0])
        else:
            result[key] = None
    if index < len(lines) and lines[index][0] > indent:
        raise TopologyParseError(f"unexpected indentation near {lines[index][1]!r}")
    return result, index


def _parse_sequence(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[object], int]:
    result: list[object] = []
    while (
        index < len(lines)
        and lines[index][0] == indent
        and lines[index][1].startswith("-")
    ):
        rest = lines[index][1][1:].strip()
        index += 1
        item_match = _KEY_RE.match(rest)
        if item_match is not None:
            # "- key: value" opens a mapping whose further keys sit at indent + 2.
            virtual = [(indent + 2, rest)]
            while index < len(lines) and lines[index][0] > indent:
                virtual.append(lines[index])
                index += 1
            mapping, _ = _parse_mapping(virtual, 0, indent + 2)
            result.append(mapping)
        else:
            result.append(_scalar(rest))
    return result, index


def parse_topology(text: str) -> dict[str, object]:
    lines = _tokenize(text)
    if not lines:
        raise TopologyParseError("topology file is empty")
    parsed, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines) or not isinstance(parsed, dict):
        raise TopologyParseError("topology file is not a single top-level mapping")
    return parsed


# --------------------------------------------------------------------------
# Topology validation
# --------------------------------------------------------------------------


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def validate_topology(text: str) -> list[str]:
    failures: list[str] = []
    try:
        topo = parse_topology(text)
    except TopologyParseError as error:
        return [f"topology: cannot parse ({error})"]

    if topo.get("name") != LAB_NAME:
        failures.append(
            f"topology: name must be {LAB_NAME!r}, got {topo.get('name')!r}"
        )

    topology = _as_dict(topo.get("topology"))
    nodes = _as_dict(topology.get("nodes"))
    expected_nodes = set(ROUTERS) | set(HOSTS)
    for missing in sorted(expected_nodes - set(nodes)):
        failures.append(f"topology: required node {missing!r} is missing")
    for extra in sorted(set(nodes) - expected_nodes):
        failures.append(f"topology: unexpected node {extra!r}")

    for name in ROUTERS:
        node = _as_dict(nodes.get(name))
        if not node:
            continue
        if node.get("kind") != "linux":
            failures.append(
                f"topology: {name} kind must be 'linux', got {node.get('kind')!r}"
            )
        if node.get("image") != ROUTER_IMAGE:
            failures.append(
                f"topology: {name} image must be {ROUTER_IMAGE!r}, got {node.get('image')!r}"
            )
        expected_binds = [
            f"frr/{name}/daemons:/etc/frr/daemons:ro",
            f"frr/{name}/frr.conf:/etc/frr/frr.conf:ro",
        ]
        if _as_list(node.get("binds")) != expected_binds:
            failures.append(
                f"topology: {name} binds must be {expected_binds}, got {node.get('binds')!r}"
            )

    for name in HOSTS:
        node = _as_dict(nodes.get(name))
        if not node:
            continue
        if node.get("kind") != "linux":
            failures.append(
                f"topology: {name} kind must be 'linux', got {node.get('kind')!r}"
            )
        if node.get("image") != HOST_IMAGE:
            failures.append(
                f"topology: {name} image must be {HOST_IMAGE!r}, got {node.get('image')!r}"
            )
        if node.get("cmd") != HOST_CMD:
            failures.append(
                f"topology: {name} cmd must be {HOST_CMD!r}, got {node.get('cmd')!r}"
            )
        if _as_list(node.get("exec")) != EXPECTED_HOST_EXEC[name]:
            failures.append(
                f"topology: {name} exec must be {EXPECTED_HOST_EXEC[name]}, "
                f"got {node.get('exec')!r}"
            )

    failures.extend(_validate_links(_as_list(topology.get("links"))))
    return failures


def _validate_links(links: list[object]) -> list[str]:
    failures: list[str] = []
    seen: list[frozenset[str]] = []
    for link in links:
        endpoints = _as_list(_as_dict(link).get("endpoints"))
        if len(endpoints) != 2 or not all(isinstance(e, str) for e in endpoints):
            failures.append(f"topology: malformed link {link!r}")
            continue
        seen.append(frozenset(str(e) for e in endpoints))

    if len(seen) != len(set(seen)):
        failures.append("topology: duplicate dataplane link present")
    for missing in sorted(EXPECTED_LINKS - set(seen), key=sorted):
        failures.append(
            f"topology: required link {' <-> '.join(sorted(missing))} is missing"
        )
    for extra in sorted(set(seen) - EXPECTED_LINKS, key=sorted):
        failures.append(
            f"topology: unexpected dataplane link {' <-> '.join(sorted(extra))}"
        )
    if len(seen) != len(EXPECTED_LINKS):
        failures.append(
            f"topology: expected {len(EXPECTED_LINKS)} links, found {len(seen)}"
        )
    return failures


# --------------------------------------------------------------------------
# FRR validation
# --------------------------------------------------------------------------


@dataclass
class ParsedFrr:
    hostname: str | None = None
    interface_addresses: dict[str, list[str]] = field(default_factory=dict)
    asn: int | None = None
    router_id: str | None = None
    neighbors: dict[str, int] = field(default_factory=dict)
    networks: set[str] = field(default_factory=set)
    maximum_paths: int | None = None
    directives: set[str] = field(default_factory=set)


def parse_frr_config(text: str) -> ParsedFrr:
    parsed = ParsedFrr()
    interface: str | None = None
    in_bgp = False
    in_af = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("!"):
            continue
        indented = raw[:1] in (" ", "\t")
        if not indented:
            interface = None
            in_bgp = False
            in_af = False
            parsed.directives.add(line)
        if line.startswith("hostname "):
            parsed.hostname = line.split(None, 1)[1]
        elif line.startswith("interface "):
            interface = line.split(None, 1)[1]
            parsed.interface_addresses.setdefault(interface, [])
        elif line.startswith("router bgp "):
            in_bgp = True
            parts = line.split()
            parsed.asn = int(parts[2]) if parts[2].isdigit() else None
        elif in_bgp and line.startswith("address-family ipv4 unicast"):
            in_af = True
        elif in_bgp and line.startswith("exit-address-family"):
            in_af = False
        elif in_bgp and line.startswith("bgp router-id "):
            parsed.router_id = line.split()[2]
        elif in_bgp and line.startswith("no bgp ebgp-requires-policy"):
            parsed.directives.add("no bgp ebgp-requires-policy")
        elif in_bgp and re.match(r"neighbor \S+ remote-as \d+$", line):
            _, ip, _, remote_as = line.split()
            parsed.neighbors[ip] = int(remote_as)
        elif in_bgp and in_af and line.startswith("network "):
            parsed.networks.add(line.split()[1])
        elif in_bgp and in_af and line.startswith("maximum-paths "):
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                parsed.maximum_paths = int(parts[1])
        elif interface is not None and line.startswith("ip address "):
            parsed.interface_addresses[interface].append(line.split()[2])
    return parsed


def validate_frr_config(router: str, text: str) -> list[str]:
    spec = ROUTER_SPECS[router]
    parsed = parse_frr_config(text)
    failures: list[str] = []
    where = f"{router}/frr.conf"

    if "frr defaults datacenter" not in parsed.directives:
        failures.append(f"{where}: missing 'frr defaults datacenter'")
    if "service integrated-vtysh-config" not in parsed.directives:
        failures.append(f"{where}: missing 'service integrated-vtysh-config'")
    if "no bgp ebgp-requires-policy" not in parsed.directives:
        failures.append(
            f"{where}: missing 'no bgp ebgp-requires-policy' (required explicitly so Lab 1 does not rely on the FRR profile default)"
        )
    if parsed.hostname != router:
        failures.append(
            f"{where}: hostname must be {router!r}, got {parsed.hostname!r}"
        )
    if parsed.asn != spec.asn:
        failures.append(f"{where}: BGP ASN must be {spec.asn}, got {parsed.asn}")

    expected_router_id = spec.loopback.split("/")[0]
    if parsed.router_id != expected_router_id:
        failures.append(
            f"{where}: router-id must be {expected_router_id}, got {parsed.router_id!r}"
        )

    expected_interfaces = {"lo": [spec.loopback]} | {
        name: [address] for name, address in spec.interfaces.items()
    }
    if parsed.interface_addresses != expected_interfaces:
        failures.append(
            f"{where}: interface addresses must be {expected_interfaces}, "
            f"got {parsed.interface_addresses}"
        )
    if parsed.neighbors != spec.neighbors:
        failures.append(
            f"{where}: BGP neighbors (ip -> remote-as) must be {spec.neighbors}, "
            f"got {parsed.neighbors}"
        )
    if parsed.networks != set(spec.networks):
        failures.append(
            f"{where}: advertised networks must be {sorted(spec.networks)}, "
            f"got {sorted(parsed.networks)}"
        )
    if spec.maximum_paths is not None and parsed.maximum_paths != spec.maximum_paths:
        failures.append(
            f"{where}: 'maximum-paths {spec.maximum_paths}' required in "
            f"address-family ipv4 unicast, got {parsed.maximum_paths}"
        )

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("!"):
            continue
        for pattern, label in _FORBIDDEN_CONFIG_PATTERNS:
            if pattern.search(line):
                failures.append(
                    f"{where}:{number}: forbidden {label} configuration: {line!r}"
                )
    return failures


def validate_daemons(router: str, text: str) -> list[str]:
    where = f"{router}/daemons"
    enabled: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and value.strip().strip('"') == "yes":
            enabled.add(key.strip())

    failures: list[str] = []
    if "bgpd" not in enabled:
        failures.append(f"{where}: bgpd=yes is required")
    if "vtysh_enable" not in enabled:
        failures.append(f"{where}: vtysh_enable=yes is required")
    for extra in sorted(enabled - _ENABLED_DAEMON_ALLOWLIST):
        failures.append(f"{where}: {extra}=yes is not permitted in Lab 1")
    return failures


# --------------------------------------------------------------------------
# Whole-lab validation
# --------------------------------------------------------------------------


def validate_lab(lab_dir: Path) -> list[str]:
    failures: list[str] = []

    topology_path = lab_dir / "topology.clab.yml"
    if topology_path.is_file():
        failures.extend(validate_topology(topology_path.read_text(encoding="utf-8")))
    else:
        failures.append(f"missing file: {topology_path}")

    for router in ROUTERS:
        for filename, validator in (
            ("daemons", validate_daemons),
            ("frr.conf", validate_frr_config),
        ):
            path = lab_dir / "frr" / router / filename
            if path.is_file():
                failures.extend(validator(router, path.read_text(encoding="utf-8")))
            else:
                failures.append(f"missing file: {path}")
    return failures


def main(argv: list[str] | None = None) -> int:
    default_lab_dir = Path(__file__).resolve().parent.parent / "lab"
    parser = argparse.ArgumentParser(description="Offline Lab 1 contract validator.")
    parser.add_argument("--lab-dir", type=Path, default=default_lab_dir)
    args = parser.parse_args(argv)

    failures = validate_lab(args.lab_dir)
    if failures:
        print(
            f"Lab 1 contract check FAILED ({len(failures)} problem(s)):",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(
        f"Lab 1 contract check passed: {len(ROUTERS)} routers, {len(HOSTS)} hosts, "
        f"{len(EXPECTED_LINKS)} links (offline; the lab has not been deployed)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
