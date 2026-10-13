import { describe, expect, test } from "vitest";
import type { DiagnosisResource } from "../api/networkDiagnosisTypes";
import {
  bgpDiagnosis,
  diagnosisResponse,
  insufficientDiagnosis,
  physicalDiagnosis,
  physicalEvidence,
} from "../test/diagnosisFixtures";
import { BASELINE_NETWORK_STATE, DEGRADED_NETWORK_STATE } from "../test/networkFixtures";
import {
  confidenceText,
  describeEvidence,
  diagnosisMarksByLink,
  findDiagnosedLink,
  groupEvidenceByRole,
  isDiagnosisAligned,
  resourceText,
} from "./diagnosisView";
import { LAB1_LINKS } from "./lab1Topology";

const LEAF1_SPINE1: DiagnosisResource = {
  resource_id: "leaf-1:eth1--spine-1:eth1",
  endpoint_a: { node_id: "leaf-1", interface: "eth1" },
  endpoint_b: { node_id: "spine-1", interface: "eth1" },
};

describe("isDiagnosisAligned", () => {
  test("aligned only on equal collection_id", () => {
    const aligned = diagnosisResponse([], { collection_id: DEGRADED_NETWORK_STATE.collection_id });
    expect(isDiagnosisAligned(aligned, DEGRADED_NETWORK_STATE)).toBe(true);
    expect(isDiagnosisAligned(aligned, BASELINE_NETWORK_STATE)).toBe(false);
  });

  test("a matching collected_at alone does not align", () => {
    const sameInstantOtherId = diagnosisResponse([], {
      collection_id: "something-else",
      collected_at: DEGRADED_NETWORK_STATE.collected_at,
    });
    expect(isDiagnosisAligned(sameInstantOtherId, DEGRADED_NETWORK_STATE)).toBe(false);
  });
});

describe("findDiagnosedLink", () => {
  test("matches exactly the declared leaf-1 eth1 <-> spine-1 eth1 link", () => {
    expect(findDiagnosedLink(LEAF1_SPINE1, LAB1_LINKS)?.id).toBe("leaf-1:eth1--spine-1:eth1");
  });

  test("matches either endpoint orientation", () => {
    const flipped: DiagnosisResource = {
      resource_id: "spine-1:eth1--leaf-1:eth1",
      endpoint_a: LEAF1_SPINE1.endpoint_b,
      endpoint_b: LEAF1_SPINE1.endpoint_a,
    };
    expect(findDiagnosedLink(flipped, LAB1_LINKS)?.id).toBe("leaf-1:eth1--spine-1:eth1");
  });

  test("does not parse resource_id: structured endpoints decide", () => {
    const liar: DiagnosisResource = { ...LEAF1_SPINE1, resource_id: "leaf-2:eth2--spine-2:eth2" };
    expect(findDiagnosedLink(liar, LAB1_LINKS)?.id).toBe("leaf-1:eth1--spine-1:eth1");
  });

  test.each([
    ["an undeclared interface", { node_id: "leaf-1", interface: "eth9" }, "spine-1", "eth1"],
    ["an undeclared node", { node_id: "leaf-9", interface: "eth1" }, "spine-1", "eth1"],
    [
      "a BGP-only pair not declared as a link",
      { node_id: "leaf-1", interface: "eth1" },
      "spine-2",
      "eth1",
    ],
  ])("returns null for %s", (_name, a, bNode, bIface) => {
    const resource: DiagnosisResource = {
      resource_id: "x",
      endpoint_a: a,
      endpoint_b: { node_id: bNode, interface: bIface },
    };
    expect(findDiagnosedLink(resource, LAB1_LINKS)).toBeNull();
  });
});

describe("diagnosisMarksByLink", () => {
  test("marks only the diagnosed link", () => {
    const marks = diagnosisMarksByLink([physicalDiagnosis()], LAB1_LINKS);
    expect([...marks.keys()]).toEqual(["leaf-1:eth1--spine-1:eth1"]);
    expect(marks.get("leaf-1:eth1--spine-1:eth1")).toEqual([
      { type: "PHYSICAL_LINK_FAILURE", confidence: "HIGH" },
    ]);
  });

  test("an unmatched resource yields no mark", () => {
    const unmatched = physicalDiagnosis({
      resource: {
        resource_id: "nowhere",
        endpoint_a: { node_id: "leaf-7", interface: "eth1" },
        endpoint_b: { node_id: "spine-7", interface: "eth1" },
      },
    });
    expect(diagnosisMarksByLink([unmatched], LAB1_LINKS).size).toBe(0);
  });

  test("keeps API order when several diagnoses name one link", () => {
    const marks = diagnosisMarksByLink([bgpDiagnosis(), physicalDiagnosis()], LAB1_LINKS);
    expect(marks.get("leaf-1:eth1--spine-1:eth1")?.map((m) => m.type)).toEqual([
      "BGP_SESSION_FAILURE",
      "PHYSICAL_LINK_FAILURE",
    ]);
  });
});

describe("text helpers", () => {
  test("confidence is never a percentage and null is 'Confidence unavailable'", () => {
    expect(confidenceText("HIGH")).toBe("HIGH confidence");
    expect(confidenceText("MEDIUM")).toBe("MEDIUM confidence");
    expect(confidenceText("LOW")).toBe("LOW confidence");
    expect(confidenceText(null)).toBe("Confidence unavailable");
  });

  test("resource text", () => {
    expect(resourceText(LEAF1_SPINE1)).toBe("leaf-1 eth1 ↔ spine-1 eth1");
  });
});

describe("describeEvidence", () => {
  const [iface, bgp, address, nextHop, ecmp, reach] = physicalEvidence();

  test("interface state", () => {
    expect(describeEvidence(iface!).facts).toEqual(["leaf-1 eth1 — DOWN"]);
  });

  test("BGP session shows the device's own state string", () => {
    expect(describeEvidence(bgp!).facts).toEqual(["leaf-1 → 10.255.0.0 — Active"]);
  });

  test("BGP session falls back to the classified state when raw_state is null", () => {
    const item = {
      ...bgp!,
      observed: { neighbor_ip: "10.255.0.0", state: "UNKNOWN", raw_state: null },
    };
    expect(describeEvidence(item as never).facts).toEqual(["leaf-1 → 10.255.0.0 — UNKNOWN"]);
  });

  test("address match", () => {
    expect(describeEvidence(address!).facts).toEqual([
      "spine-1 eth1: declared and observed interface address match",
    ]);
    const mismatch = {
      ...address!,
      observed: { ...address!.observed, matches: false, observed_addresses: ["10.9.9.9"] },
    };
    expect(describeEvidence(mismatch as never).facts).toEqual([
      "spine-1 eth1: declared 10.255.0.0; observed 10.9.9.9",
    ]);
  });

  test("route next hop", () => {
    expect(describeEvidence(nextHop!).facts).toEqual([
      "leaf-1: 10.255.0.0 — absent from active routes",
    ]);
  });

  test("ECMP paths", () => {
    expect(describeEvidence(ecmp!).facts).toEqual([
      "leaf-1: 10.1.2.0/24 — 1 of 2 paths",
      "path via 10.255.0.0 absent",
    ]);
  });

  test("reachability", () => {
    expect(describeEvidence(reach!).facts).toEqual(["host-1 → 10.1.2.10 — 3/3 received"]);
  });

  test("unobserved values are stated, never invented", () => {
    const unobserved = {
      ...reach!,
      observed: { target: "10.1.2.10", sent: null, received: null },
    };
    expect(describeEvidence(unobserved as never).facts).toEqual([
      "host-1 → 10.1.2.10 — not observed",
    ]);
  });

  test("no causal wording in any rendered fact", () => {
    const items = [
      ...physicalEvidence(),
      ...bgpDiagnosis().evidence,
      ...insufficientDiagnosis().evidence,
    ];
    const text = items.flatMap((item) => describeEvidence(item).facts).join(" ");
    expect(text).not.toMatch(/proves?|caused|because|therefore/i);
  });
});

describe("groupEvidenceByRole", () => {
  test("groups by role and preserves engine order within each group", () => {
    const groups = groupEvidenceByRole(physicalEvidence());
    expect(groups.SUPPORTS.map((e) => e.kind)).toEqual([
      "INTERFACE_STATE",
      "BGP_SESSION",
      "ROUTE_NEXT_HOP",
      "ECMP_PATHS",
    ]);
    expect(groups.CONTRADICTS.map((e) => e.kind)).toEqual(["ADDRESS_MATCH"]);
    expect(groups.UNKNOWN.map((e) => e.kind)).toEqual(["REACHABILITY"]);
  });
});
