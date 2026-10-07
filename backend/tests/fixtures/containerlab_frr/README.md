# Containerlab/FRR captured fixtures (NPE-1C3A)

Raw, unedited stdout of read-only commands run with `docker exec` against a
live Lab 1 (`lab/topology.clab.yml`, Containerlab 0.77.0, native Docker Engine
in Ubuntu WSL2) on 2026-10-07. Nothing was modified by hand. They contain
local Lab 1 data only (nothing from the host machine or any other network):
node hostnames, RFC1918/ULA lab addresses, and container-generated MACs. There
are no credentials or external information.

Images: `quay.io/frrouting/frr:10.6.1` (routers), `alpine:3.24.2` (hosts,
BusyBox v1.37.0 `ip`, which has no `-j`).

## Commands

| File | Command | Nodes |
|---|---|---|
| `ip_j_addr.json` | `ip -j addr` | routers |
| `show_bgp_summary.json` | `vtysh -c "show bgp summary json"` | routers |
| `show_ip_route.json` | `vtysh -c "show ip route json"` | routers |
| `ip_o_link.txt` | `ip -o link show` | hosts |
| `ip_o_addr.txt` | `ip -o addr show` | hosts |
| `ping_<target>.txt` | `ping -c 3 -W 2 <target>` | hosts |

## Sets

- `baseline/` — healthy lab: 4/4 eBGP Established, two-path ECMP on both
  leaves, all six nodes.
- `degraded-active/` — all six nodes, captured during one run of the existing,
  unmodified `scripts/lab_failure_scenario.py` (leaf-1 eth1 down) by a
  concurrent read-only sampler. leaf-1 and spine-1 report the failed session as
  `Active`; leaf-2 is down to one path; hosts still reach each other.
- `degraded-idle/`, `degraded-clearing/` — leaf-1 and spine-1 only, from an
  earlier run of the same harness: the failed neighbor as `Idle`, and as
  `Clearing` (a transient FRR state that is *not* in the normalized state
  enum, so it normalizes to `UNKNOWN` with `raw_state="Clearing"`).

The degraded sets show two real-world traps the parsers handle:

- mid-convergence, the FRR RIB still lists the failed next-hop
  (`10.255.0.0 via eth1`) with `fib: true` but no `active` key;
- FRR reports `peerUptimeMsec` (time in the current state) and `pfxRcd` for
  sessions that are not Established.

The collector only trusts a next-hop with `active: true`, and reports
uptime/prefix counts only for an Established session.

Some files are byte-identical across sets (for example `ip_j_addr.json` for
nodes whose interfaces did not change during the failure). That is the real
observation, kept so every set is a complete, self-contained capture.
