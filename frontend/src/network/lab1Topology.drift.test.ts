/// <reference types="node" />
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import {
  LAB1_ECMP_EXPECTATIONS,
  LAB1_LINKS,
  LAB1_NODES,
  expectedAdjacencyCount,
} from "./lab1Topology";

/**
 * Drift guard: the frontend's declared Lab 1 topology must equal the
 * repository's own definition. Test-only, dependency-free parsing of the
 * (very regular) `lab/topology.clab.yml`, the FRR interface addresses in
 * `lab/frr/<router>/frr.conf`, and `backend/src/meta_rne/lab1_identity.py`.
 * Production code never touches the filesystem or a YAML parser.
 */
function repoFile(relative: string): string {
  // Plain path arithmetic (not `new URL(.., import.meta.url)`, which Vite
  // would treat as a static asset reference).
  const here = dirname(fileURLToPath(import.meta.url));
  return readFileSync(resolve(here, "..", "..", "..", relative), "utf-8");
}

const topologyYaml = repoFile("lab/topology.clab.yml");

function parseNodeNames(yaml: string): string[] {
  const section = yaml.split(/^ {2}nodes:\s*$/m)[1]!.split(/^ {2}links:\s*$/m)[0]!;
  return [...section.matchAll(/^ {4}([\w-]+):\s*$/gm)].map((m) => m[1]!);
}

function parseLinks(yaml: string): [string, string][] {
  const section = yaml.split(/^ {2}links:\s*$/m)[1]!;
  return [...section.matchAll(/endpoints:\s*\["([^"]+)",\s*"([^"]+)"\]/g)].map((m) => [
    m[1]!,
    m[2]!,
  ]);
}

/** `node:iface` -> IPv4 address (no prefix length) from frr.conf / host exec lines. */
function parseAddresses(): Map<string, string> {
  const result = new Map<string, string>();
  for (const router of ["spine-1", "spine-2", "leaf-1", "leaf-2"]) {
    let iface: string | null = null;
    for (const line of repoFile(`lab/frr/${router}/frr.conf`).split(/\r?\n/)) {
      const ifaceMatch = /^interface (\S+)$/.exec(line);
      if (ifaceMatch) {
        iface = ifaceMatch[1]!;
        continue;
      }
      const addr = /^ ip address (\d+\.\d+\.\d+\.\d+)\/\d+$/.exec(line);
      if (addr && iface !== null) {
        result.set(`${router}:${iface}`, addr[1]!);
      }
      if (/^router /.test(line)) {
        iface = null;
      }
    }
  }
  for (const m of topologyYaml.matchAll(
    /^ {4}(host-\d):[\s\S]*?ip addr add (\d+\.\d+\.\d+\.\d+)\/\d+ dev (\w+)/gm,
  )) {
    result.set(`${m[1]!}:${m[3]!}`, m[2]!);
  }
  return result;
}

const sortedPairs = (pairs: [string, string][]) =>
  pairs.map((pair) => [...pair].sort().join(" <-> ")).sort();

describe("declared Lab 1 topology matches lab/topology.clab.yml", () => {
  test("nodes are exactly the six in the topology file", () => {
    expect(LAB1_NODES.map((n) => n.id).sort()).toEqual(parseNodeNames(topologyYaml).sort());
    expect(LAB1_NODES).toHaveLength(6);
  });

  test("links are exactly the six endpoint pairs in the topology file", () => {
    const declared = LAB1_LINKS.map((l): [string, string] => [
      `${l.a.node}:${l.a.interface}`,
      `${l.b.node}:${l.b.interface}`,
    ]);
    expect(LAB1_LINKS).toHaveLength(6);
    expect(sortedPairs(declared)).toEqual(sortedPairs(parseLinks(topologyYaml)));
  });

  test("link kinds: router-router are fabric, host links are access", () => {
    for (const link of LAB1_LINKS) {
      const hostEnd = link.a.node.startsWith("host-") || link.b.node.startsWith("host-");
      expect(link.kind).toBe(hostEnd ? "access" : "fabric");
    }
  });

  test("every endpoint address matches frr.conf / the host exec lines", () => {
    const addresses = parseAddresses();
    for (const link of LAB1_LINKS) {
      for (const end of [link.a, link.b]) {
        expect(addresses.get(`${end.node}:${end.interface}`), `${end.node}:${end.interface}`).toBe(
          end.address,
        );
      }
    }
  });

  test("each fabric link is an eBGP adjacency in frr.conf on both routers", () => {
    for (const link of LAB1_LINKS.filter((l) => l.kind === "fabric")) {
      for (const [self, peer] of [
        [link.a, link.b],
        [link.b, link.a],
      ] as const) {
        expect(repoFile(`lab/frr/${self.node}/frr.conf`)).toMatch(
          new RegExp(`neighbor ${peer.address.replaceAll(".", "\\.")} remote-as \\d+`),
        );
      }
    }
    expect(expectedAdjacencyCount("leaf-1")).toBe(2);
    expect(expectedAdjacencyCount("spine-2")).toBe(2);
  });

  test("router device IDs match backend lab1_identity.py; hosts have none", () => {
    const identity = repoFile("backend/src/meta_rne/lab1_identity.py");
    const mapping = Object.fromEntries(
      [...identity.matchAll(/"([\w-]+)":\s*"([\w-]+)"/g)].map((m) => [m[1]!, m[2]!]),
    );
    for (const node of LAB1_NODES) {
      if (node.role === "router") {
        expect(node.deviceId).toBe(mapping[node.id]);
      } else {
        expect(node.deviceId).toBeNull();
      }
    }
  });

  test("ECMP expectations: the other host's /24, expected paths = spines = maximum-paths", () => {
    const addresses = parseAddresses();
    for (const expectation of LAB1_ECMP_EXPECTATIONS) {
      const hostAddress = addresses.get(`${expectation.towardsHost}:eth1`)!;
      const network = `${hostAddress.split(".").slice(0, 3).join(".")}.0/24`;
      expect(expectation.prefix).toBe(network);
      expect(repoFile(`lab/frr/${expectation.node}/frr.conf`)).toMatch(
        new RegExp(`maximum-paths ${expectation.expectedPaths}\\b`),
      );
      const spines = LAB1_NODES.filter((n) => n.tier === "spine").length;
      expect(expectation.expectedPaths).toBe(spines);
    }
  });
});
