import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ApiRequestError, MALFORMED_RESPONSE_MESSAGE } from "./client";
import { fetchNetworkDiagnosis } from "./networkDiagnosis";
import { isNetworkDiagnosisResponse, type DiagnosisEvidence } from "./networkDiagnosisTypes";
import { isOperationalStateNotFound } from "./networkState";
import {
  bgpDiagnosis,
  diagnosisResponse,
  healthyDiagnosis,
  insufficientDiagnosis,
  linkedIncident,
  physicalDiagnosis,
  physicalEvidence,
} from "../test/diagnosisFixtures";
import { jsonResponse, notFoundNoSnapshotResponse } from "../test/networkFixtures";

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("fetchNetworkDiagnosis: request", () => {
  test("is a read-only GET /network/diagnosis with no body", async () => {
    const fetchMock = stubFetch(jsonResponse(healthyDiagnosis()));
    await fetchNetworkDiagnosis();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8080/network/diagnosis");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  test("passes the abort signal through", async () => {
    const fetchMock = stubFetch(jsonResponse(healthyDiagnosis()));
    const controller = new AbortController();
    await fetchNetworkDiagnosis({ signal: controller.signal });
    expect((fetchMock.mock.calls[0]![1] as RequestInit).signal).toBe(controller.signal);
  });
});

describe("fetchNetworkDiagnosis: responses", () => {
  test("healthy response: empty diagnoses", async () => {
    stubFetch(jsonResponse(healthyDiagnosis()));
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses).toEqual([]);
    expect(result.collection_id).toBe("collection-baseline");
    expect(result.collected_at).toBe("2026-10-07T12:00:00Z");
  });

  test("physical diagnosis keeps resource, confidence, evidence, symptom keys and incidents", async () => {
    const body = diagnosisResponse([physicalDiagnosis()]);
    stubFetch(jsonResponse(body));
    const result = await fetchNetworkDiagnosis();
    expect(result).toEqual(body);
    const [diagnosis] = result.diagnoses;
    expect(diagnosis!.diagnosis_type).toBe("PHYSICAL_LINK_FAILURE");
    expect(diagnosis!.confidence).toBe("HIGH");
    expect(diagnosis!.resource.endpoint_a).toEqual({ node_id: "leaf-1", interface: "eth1" });
    expect(diagnosis!.resource.endpoint_b).toEqual({ node_id: "spine-1", interface: "eth1" });
    expect(diagnosis!.symptom_keys).toHaveLength(2);
  });

  test("BGP diagnosis", async () => {
    stubFetch(jsonResponse(diagnosisResponse([bgpDiagnosis()])));
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses[0]!.diagnosis_type).toBe("BGP_SESSION_FAILURE");
    expect(result.diagnoses[0]!.confidence).toBe("MEDIUM");
  });

  test("INSUFFICIENT_EVIDENCE carries a null confidence", async () => {
    stubFetch(jsonResponse(diagnosisResponse([insufficientDiagnosis()])));
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses[0]!.diagnosis_type).toBe("INSUFFICIENT_EVIDENCE");
    expect(result.diagnoses[0]!.confidence).toBeNull();
  });

  test("every evidence kind and role round-trips, API order preserved", async () => {
    const body = diagnosisResponse([physicalDiagnosis(), insufficientDiagnosis()]);
    stubFetch(jsonResponse(body));
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses.map((d) => d.diagnosis_type)).toEqual([
      "PHYSICAL_LINK_FAILURE",
      "INSUFFICIENT_EVIDENCE",
    ]);
    const kinds = result.diagnoses[0]!.evidence.map((e) => e.kind);
    expect(kinds).toEqual([
      "INTERFACE_STATE",
      "BGP_SESSION",
      "ADDRESS_MATCH",
      "ROUTE_NEXT_HOP",
      "ECMP_PATHS",
      "REACHABILITY",
    ]);
    const roles = new Set(result.diagnoses[0]!.evidence.map((e) => e.role));
    expect(roles).toEqual(new Set(["SUPPORTS", "CONTRADICTS", "UNKNOWN"]));
  });

  test("nullable observed values are accepted as null", () => {
    const evidence: DiagnosisEvidence[] = [
      {
        kind: "BGP_SESSION",
        role: "UNKNOWN",
        node_id: "leaf-1",
        subject: "10.255.0.0",
        observed: { neighbor_ip: "10.255.0.0", state: "UNKNOWN", raw_state: null },
      },
      {
        kind: "ADDRESS_MATCH",
        role: "UNKNOWN",
        node_id: "leaf-1",
        subject: "eth1",
        observed: {
          interface_name: "eth1",
          declared_address: "10.255.0.1",
          observed_addresses: [],
          matches: null,
        },
      },
      {
        kind: "ECMP_PATHS",
        role: "UNKNOWN",
        node_id: "leaf-1",
        subject: "10.1.2.0/24",
        observed: {
          prefix: "10.1.2.0/24",
          observed_paths: null,
          expected_paths: 2,
          link_next_hop_ip: "10.255.0.0",
          link_next_hop_present: null,
        },
      },
      {
        kind: "REACHABILITY",
        role: "UNKNOWN",
        node_id: "host-1",
        subject: "10.1.2.10",
        observed: { target: "10.1.2.10", sent: null, received: null },
      },
      {
        kind: "ROUTE_NEXT_HOP",
        role: "UNKNOWN",
        node_id: "leaf-1",
        subject: "10.255.0.0",
        observed: { next_hop_ip: "10.255.0.0", interface_name: "eth1", active_route_count: null },
      },
    ];
    expect(
      isNetworkDiagnosisResponse(
        diagnosisResponse([physicalDiagnosis({ evidence }), insufficientDiagnosis({ evidence })]),
      ),
    ).toBe(true);
  });

  test("linked-incident fields are preserved exactly", async () => {
    const incident = linkedIncident("lab1-leaf-1", "10.255.0.0", {
      occurrence_count: 4,
      status: "OPEN",
      severity: "Critical",
      last_seen_at: "2026-10-07T12:03:00Z",
    });
    stubFetch(
      jsonResponse(diagnosisResponse([physicalDiagnosis({ linked_incidents: [incident] })])),
    );
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses[0]!.linked_incidents).toEqual([incident]);
  });

  test("zero linked incidents is valid", async () => {
    stubFetch(jsonResponse(diagnosisResponse([physicalDiagnosis({ linked_incidents: [] })])));
    const result = await fetchNetworkDiagnosis();
    expect(result.diagnoses[0]!.linked_incidents).toEqual([]);
  });
});

describe("fetchNetworkDiagnosis: errors", () => {
  test("404 operational_state_not_found stays distinguishable", async () => {
    stubFetch(notFoundNoSnapshotResponse());
    const error = await fetchNetworkDiagnosis().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(isOperationalStateNotFound(error)).toBe(true);
  });

  test("a generic API failure keeps its code but is not 'not found'", async () => {
    stubFetch(jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500));
    const error = await fetchNetworkDiagnosis().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("persistence_error");
    expect(isOperationalStateNotFound(error)).toBe(false);
  });

  test("a non-JSON 2xx body is the stable malformed error", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(fetchNetworkDiagnosis()).rejects.toThrow(MALFORMED_RESPONSE_MESSAGE);
  });

  test.each([
    ["a JSON array", []],
    ["missing diagnoses", { collection_id: "c", collected_at: "2026-10-07T12:00:00Z" }],
    ["unparseable collected_at", { collection_id: "c", collected_at: "not-a-date", diagnoses: [] }],
    [
      "blank collection_id",
      { collection_id: "", collected_at: "2026-10-07T12:00:00Z", diagnoses: [] },
    ],
    [
      "unknown diagnosis type",
      diagnosisResponse([{ ...physicalDiagnosis(), diagnosis_type: "MAGIC" } as never]),
    ],
    [
      "unknown confidence",
      diagnosisResponse([{ ...physicalDiagnosis(), confidence: "CERTAIN" } as never]),
    ],
    [
      "null confidence on a physical diagnosis",
      diagnosisResponse([{ ...physicalDiagnosis(), confidence: null }]),
    ],
    [
      "confidence on INSUFFICIENT_EVIDENCE",
      diagnosisResponse([{ ...insufficientDiagnosis(), confidence: "LOW" }]),
    ],
    [
      "missing endpoint",
      diagnosisResponse([
        {
          ...physicalDiagnosis(),
          resource: { resource_id: "x", endpoint_a: { node_id: "leaf-1", interface: "eth1" } },
        } as never,
      ]),
    ],
    [
      "unknown evidence kind",
      diagnosisResponse([
        physicalDiagnosis({ evidence: [{ ...physicalEvidence()[0]!, kind: "MAGIC" } as never] }),
      ]),
    ],
    [
      "observed shape that does not fit the kind",
      diagnosisResponse([
        physicalDiagnosis({
          evidence: [
            { ...physicalEvidence()[0]!, observed: { target: "x", sent: 1, received: 1 } } as never,
          ],
        }),
      ]),
    ],
    [
      "unknown evidence role",
      diagnosisResponse([
        physicalDiagnosis({ evidence: [{ ...physicalEvidence()[0]!, role: "PROVES" } as never] }),
      ]),
    ],
    [
      "linked incident with a bad timestamp",
      diagnosisResponse([
        physicalDiagnosis({
          linked_incidents: [
            { ...linkedIncident("lab1-leaf-1", "10.255.0.0"), created_at: "nope" },
          ],
        }),
      ]),
    ],
    [
      "linked incident with a negative occurrence_count",
      diagnosisResponse([
        physicalDiagnosis({
          linked_incidents: [
            { ...linkedIncident("lab1-leaf-1", "10.255.0.0"), occurrence_count: -1 },
          ],
        }),
      ]),
    ],
  ])("rejects %s as malformed", async (_name, body) => {
    stubFetch(jsonResponse(body));
    await expect(fetchNetworkDiagnosis()).rejects.toThrow(MALFORMED_RESPONSE_MESSAGE);
  });
});
