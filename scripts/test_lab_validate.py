#!/usr/bin/env python3
"""Unit tests for scripts/lab_validate.py (NPE-1B2).

Standard-library ``unittest`` only, matching scripts/test_demo.py. Needs no
Docker daemon, Containerlab deployment, FRR/Alpine image, network access, or
root. Every mutation test works on a temporary copy of the committed
``lab/`` directory — the committed files are never modified.

Run directly:
    python scripts/test_lab_validate.py
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_validate  # noqa: E402

COMMITTED_LAB = Path(__file__).resolve().parent.parent / "lab"


class LabCopyTestCase(unittest.TestCase):
    """Provides a throwaway copy of the committed lab/ directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lab = Path(self._tmp.name) / "lab"
        shutil.copytree(COMMITTED_LAB, self.lab)

    def edit(self, relative: str, old: str, new: str) -> None:
        path = self.lab / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"fixture drift: {old!r} not found in {relative}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def append(self, relative: str, extra: str) -> None:
        path = self.lab / relative
        path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")

    def assert_fails_with(self, fragment: str) -> None:
        failures = lab_validate.validate_lab(self.lab)
        self.assertTrue(failures, "expected a contract failure, got none")
        self.assertTrue(
            any(fragment in failure for failure in failures),
            f"no failure mentions {fragment!r}; got {failures}",
        )


class CommittedLabTests(unittest.TestCase):
    def test_committed_lab__passes_contract(self) -> None:
        self.assertEqual(lab_validate.validate_lab(COMMITTED_LAB), [])

    def test_main__returns_zero_for_committed_lab(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = lab_validate.main(["--lab-dir", str(COMMITTED_LAB)])
        self.assertEqual(code, 0)
        self.assertIn("passed", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_main__returns_nonzero_and_prints_failures_for_missing_lab(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = lab_validate.main(["--lab-dir", empty])
        self.assertNotEqual(code, 0)
        self.assertIn("missing file", err.getvalue())


class TopologyContractTests(LabCopyTestCase):
    def test_missing_node__fails(self) -> None:
        text = (self.lab / "topology.clab.yml").read_text(encoding="utf-8")
        start = text.index("    host-2:")
        end = text.index("\n  links:")
        (self.lab / "topology.clab.yml").write_text(
            text[:start] + text[end:], encoding="utf-8"
        )
        self.assert_fails_with("required node 'host-2' is missing")

    def test_wrong_router_image__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "image: quay.io/frrouting/frr:10.6.1",
            "image: quay.io/frrouting/frr:latest",
        )
        self.assert_fails_with("image must be 'quay.io/frrouting/frr:10.6.1'")

    def test_wrong_host_image__fails(self) -> None:
        self.edit("topology.clab.yml", "image: alpine:3.24.2", "image: alpine:latest")
        self.assert_fails_with("image must be 'alpine:3.24.2'")

    def test_wrong_host_gateway__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "ip route replace default via 10.1.1.1 dev eth1",
            "ip route replace default via 10.1.1.254 dev eth1",
        )
        self.assert_fails_with("host-1 exec must be")

    def test_wrong_host_address__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "ip addr add 10.1.2.10/24 dev eth1",
            "ip addr add 10.1.2.11/24 dev eth1",
        )
        self.assert_fails_with("host-2 exec must be")

    def test_unexpected_extra_dataplane_link__fails(self) -> None:
        self.append(
            "topology.clab.yml",
            '    - endpoints: ["spine-1:eth3", "spine-2:eth3"]\n',
        )
        self.assert_fails_with(
            "unexpected dataplane link spine-1:eth3 <-> spine-2:eth3"
        )

    def test_missing_link__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            '    - endpoints: ["leaf-2:eth2", "spine-2:eth2"]\n',
            "",
        )
        self.assert_fails_with("required link leaf-2:eth2 <-> spine-2:eth2 is missing")

    def test_wrong_bind__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "frr/leaf-1/frr.conf:/etc/frr/frr.conf:ro",
            "frr/leaf-1/frr.conf:/etc/frr/frr.conf",
        )
        self.assert_fails_with("leaf-1 binds must be")


class MgmtContractTests(LabCopyTestCase):
    def test_missing_mgmt_block__fails(self) -> None:
        text = (self.lab / "topology.clab.yml").read_text(encoding="utf-8")
        start = text.index("mgmt:")
        end = text.index("topology:")
        (self.lab / "topology.clab.yml").write_text(
            text[:start] + text[end:], encoding="utf-8"
        )
        self.assert_fails_with("mgmt network must be 'meta-rne-bgp-mgmt'")

    def test_wrong_mgmt_network_name__fails(self) -> None:
        self.edit(
            "topology.clab.yml", "network: meta-rne-bgp-mgmt", "network: clab-mgmt"
        )
        self.assert_fails_with("mgmt network must be 'meta-rne-bgp-mgmt'")

    def test_wrong_mgmt_ipv4_subnet__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "ipv4-subnet: 172.31.250.0/24",
            "ipv4-subnet: 172.20.20.0/24",
        )
        self.assert_fails_with("mgmt ipv4-subnet must be '172.31.250.0/24'")

    def test_wrong_mgmt_ipv6_subnet__fails(self) -> None:
        self.edit(
            "topology.clab.yml",
            "ipv6-subnet: 3fff:172:31:250::/64",
            "ipv6-subnet: 3fff:172:20:20::/64",
        )
        self.assert_fails_with("mgmt ipv6-subnet must be '3fff:172:31:250::/64'")


class FrrContractTests(LabCopyTestCase):
    def test_wrong_asn__fails(self) -> None:
        self.edit("frr/leaf-1/frr.conf", "router bgp 65101", "router bgp 65999")
        self.assert_fails_with("BGP ASN must be 65101")

    def test_wrong_interface_address__fails(self) -> None:
        self.edit(
            "frr/leaf-1/frr.conf",
            "ip address 10.255.0.1/31",
            "ip address 10.255.0.9/31",
        )
        self.assert_fails_with("interface addresses must be")

    def test_wrong_neighbor_remote_as__fails(self) -> None:
        self.edit(
            "frr/spine-1/frr.conf",
            "neighbor 10.255.0.1 remote-as 65101",
            "neighbor 10.255.0.1 remote-as 65102",
        )
        self.assert_fails_with("BGP neighbors (ip -> remote-as) must be")

    def test_missing_advertised_host_network__fails(self) -> None:
        self.edit("frr/leaf-2/frr.conf", "  network 10.1.2.0/24\n", "")
        self.assert_fails_with("advertised networks must be")

    def test_missing_maximum_paths__fails(self) -> None:
        self.edit("frr/leaf-1/frr.conf", "  maximum-paths 2\n", "")
        self.assert_fails_with("'maximum-paths 2' required")

    def test_wrong_maximum_paths__fails(self) -> None:
        self.edit("frr/leaf-2/frr.conf", "maximum-paths 2", "maximum-paths 4")
        self.assert_fails_with("'maximum-paths 2' required")

    def test_spine_without_maximum_paths__passes(self) -> None:
        self.assertNotIn(
            "maximum-paths", (self.lab / "frr/spine-1/frr.conf").read_text("utf-8")
        )
        self.assertEqual(lab_validate.validate_lab(self.lab), [])

    def test_wrong_hostname__fails(self) -> None:
        self.edit("frr/spine-2/frr.conf", "hostname spine-2", "hostname spine-9")
        self.assert_fails_with("hostname must be 'spine-2'")

    def test_wrong_loopback__fails(self) -> None:
        self.edit(
            "frr/spine-1/frr.conf", "ip address 10.0.0.1/32", "ip address 10.0.0.9/32"
        )
        self.assert_fails_with("interface addresses must be")

    def test_missing_ebgp_policy_knob__fails(self) -> None:
        self.edit("frr/leaf-1/frr.conf", " no bgp ebgp-requires-policy\n", "")
        self.assert_fails_with("no bgp ebgp-requires-policy")

    def test_forbidden_protocol_config__fails(self) -> None:
        cases = {
            "OSPF": "router ospf\n network 10.0.0.0/8 area 0\n",
            "IS-IS": "router isis LAB\n net 49.0001.0000.0000.0001.00\n",
            "MPLS/LDP": "mpls ldp\n router-id 10.0.0.1\n",
            "GRE": "interface gre1\n description gre tunnel\n",
            "IPIP": "interface ipip1\n description ipip tunnel\n",
        }
        for label, snippet in cases.items():
            with self.subTest(protocol=label):
                self.setUp()
                self.append("frr/spine-1/frr.conf", snippet)
                self.assert_fails_with(f"forbidden {label} configuration")

    def test_forbidden_daemon_enabled__fails(self) -> None:
        self.edit("frr/spine-1/daemons", "ospfd=no", "ospfd=yes")
        self.assert_fails_with("ospfd=yes is not permitted")

    def test_bgpd_disabled__fails(self) -> None:
        self.edit("frr/leaf-1/daemons", "bgpd=yes", "bgpd=no")
        self.assert_fails_with("bgpd=yes is required")

    def test_missing_frr_config_file__fails(self) -> None:
        (self.lab / "frr" / "leaf-2" / "frr.conf").unlink()
        self.assert_fails_with("missing file")

    def test_missing_daemons_file__fails(self) -> None:
        (self.lab / "frr" / "spine-2" / "daemons").unlink()
        self.assert_fails_with("missing file")


class ParserTests(unittest.TestCase):
    def test_parse_topology__reads_nested_mapping_lists_and_flow_sequences(
        self,
    ) -> None:
        parsed = lab_validate.parse_topology(
            "name: demo\ntopology:\n  nodes:\n    a:\n      binds:\n        - x:/y:ro\n"
            '  links:\n    - endpoints: ["a:eth1", "b:eth1"]\n'
        )
        self.assertEqual(parsed["name"], "demo")
        topology = parsed["topology"]
        assert isinstance(topology, dict)
        self.assertEqual(topology["nodes"], {"a": {"binds": ["x:/y:ro"]}})
        self.assertEqual(topology["links"], [{"endpoints": ["a:eth1", "b:eth1"]}])

    def test_parse_topology__rejects_tab_indentation(self) -> None:
        with self.assertRaises(lab_validate.TopologyParseError):
            lab_validate.parse_topology("name: x\ntopology:\n\tnodes:\n")

    def test_unparseable_topology__reported_as_failure(self) -> None:
        self.assertTrue(lab_validate.validate_topology("not a mapping line\n"))


class CommittedFilesUntouchedTests(unittest.TestCase):
    def test_mutation_tests_do_not_modify_committed_lab(self) -> None:
        before = {p: p.read_bytes() for p in COMMITTED_LAB.rglob("*") if p.is_file()}
        lab_validate.validate_lab(COMMITTED_LAB)
        after = {p: p.read_bytes() for p in COMMITTED_LAB.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
