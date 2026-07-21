import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchIncidents, resolveIncident } from "./incidents";
import { ApiRequestError } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const validIncident = {
  incident_id: "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
  fingerprint: "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
  device_id: "spine-01",
  source: "POLICY_VIOLATION",
  rule_ref: "policy-acl-external-in",
  affected_resource: "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
  severity: "Medium",
  status: "OPEN",
  evidence: {
    source_snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    violation_type: "MISSING_REQUIRED_ACL",
    expected_acl_name: "ACL-EXTERNAL-IN",
    actual_acl_name: null,
    interface_name: "GigabitEthernet0/1",
    direction: "in",
  },
  recommendation: "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  created_at: "2026-07-18T10:00:00Z",
  last_seen_at: "2026-07-18T10:00:00Z",
  occurrence_count: 1,
  updated_at: "2026-07-18T10:00:00Z",
  resolved_at: null,
};

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function stubIncidentsResponse(body: unknown, status = 200): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(body, status)));
}

test("returns validated incidents when the payload matches the contract", async () => {
  stubIncidentsResponse([validIncident]);

  const result = await fetchIncidents();

  expect(result).toEqual([validIncident]);
});

test("rejects an array element missing a required field (fingerprint)", async () => {
  const { fingerprint, ...withoutFingerprint } = validIncident;
  void fingerprint;
  stubIncidentsResponse([withoutFingerprint]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an array element with a wrong-typed field (occurrence_count as a string)", async () => {
  stubIncidentsResponse([{ ...validIncident, occurrence_count: "1" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an array element with a malformed nested evidence object", async () => {
  stubIncidentsResponse([
    { ...validIncident, evidence: { source_snapshot_id: "only-this-field" } },
  ]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("accepts an incident with an unrecognized future enum value (forward-compatible)", async () => {
  const futureIncident = { ...validIncident, severity: "Informational" };
  stubIncidentsResponse([futureIncident]);

  const result = await fetchIncidents();

  expect(result).toEqual([futureIncident]);
});

test("rejects a top-level object instead of an array", async () => {
  stubIncidentsResponse({ ...validIncident });

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects a null entry inside the incidents array", async () => {
  stubIncidentsResponse([validIncident, null]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects occurrence_count when it is a non-integer number", async () => {
  stubIncidentsResponse([{ ...validIncident, occurrence_count: 1.5 }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects occurrence_count when it is negative", async () => {
  stubIncidentsResponse([{ ...validIncident, occurrence_count: -1 }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an incident whose evidence key is missing entirely", async () => {
  const { evidence, ...withoutEvidence } = validIncident;
  void evidence;
  stubIncidentsResponse([withoutEvidence]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an incident whose evidence is null", async () => {
  stubIncidentsResponse([{ ...validIncident, evidence: null }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("accepts a null actual_acl_name inside evidence", async () => {
  const incidentWithNullAcl = {
    ...validIncident,
    evidence: { ...validIncident.evidence, actual_acl_name: null },
  };
  stubIncidentsResponse([incidentWithNullAcl]);

  const result = await fetchIncidents();

  expect(result).toEqual([incidentWithNullAcl]);
});

test("rejects an empty-string severity", async () => {
  stubIncidentsResponse([{ ...validIncident, severity: "" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string status", async () => {
  stubIncidentsResponse([{ ...validIncident, status: "" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string source", async () => {
  stubIncidentsResponse([{ ...validIncident, source: "" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string violation_type", async () => {
  stubIncidentsResponse([
    { ...validIncident, evidence: { ...validIncident.evidence, violation_type: "" } },
  ]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string direction", async () => {
  stubIncidentsResponse([
    { ...validIncident, evidence: { ...validIncident.evidence, direction: "" } },
  ]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("preserves an unknown future severity value as text", async () => {
  const incident = { ...validIncident, severity: "Informational" };
  stubIncidentsResponse([incident]);

  const result = await fetchIncidents();

  expect(result[0]?.severity).toBe("Informational");
});

test("preserves an unknown future status value as text", async () => {
  const incident = { ...validIncident, status: "SUPPRESSED" };
  stubIncidentsResponse([incident]);

  const result = await fetchIncidents();

  expect(result[0]?.status).toBe("SUPPRESSED");
});

test("preserves an unknown future source value as text", async () => {
  const incident = { ...validIncident, source: "TELEMETRY_HEURISTIC" };
  stubIncidentsResponse([incident]);

  const result = await fetchIncidents();

  expect(result[0]?.source).toBe("TELEMETRY_HEURISTIC");
});

test("preserves an unknown future violation_type value as text", async () => {
  const incident = {
    ...validIncident,
    evidence: { ...validIncident.evidence, violation_type: "UNEXPECTED_BGP_NEIGHBOR" },
  };
  stubIncidentsResponse([incident]);

  const result = await fetchIncidents();

  expect(result[0]?.evidence.violation_type).toBe("UNEXPECTED_BGP_NEIGHBOR");
});

test("preserves an unknown future direction value as text", async () => {
  const incident = {
    ...validIncident,
    evidence: { ...validIncident.evidence, direction: "both" },
  };
  stubIncidentsResponse([incident]);

  const result = await fetchIncidents();

  expect(result[0]?.evidence.direction).toBe("both");
});

test("preserves backend order across multiple valid incidents", async () => {
  const first = { ...validIncident, incident_id: "id-1", fingerprint: "fp-1" };
  const second = { ...validIncident, incident_id: "id-2", fingerprint: "fp-2" };
  const third = { ...validIncident, incident_id: "id-3", fingerprint: "fp-3" };
  stubIncidentsResponse([third, first, second]);

  const result = await fetchIncidents();

  expect(result.map((incident) => incident.incident_id)).toEqual(["id-3", "id-1", "id-2"]);
});

test("leaves opaque IDs byte-for-byte unchanged", async () => {
  const opaqueIncident = {
    ...validIncident,
    incident_id: "  weird-ID_with.Punct+uation==\t",
    fingerprint: "0123456789ABCDEFabcdef",
    device_id: "spine-01 (rack 4)",
  };
  stubIncidentsResponse([opaqueIncident]);

  const result = await fetchIncidents();

  expect(result[0]?.incident_id).toBe(opaqueIncident.incident_id);
  expect(result[0]?.fingerprint).toBe(opaqueIncident.fingerprint);
  expect(result[0]?.device_id).toBe(opaqueIncident.device_id);
});

// --- updated_at / resolved_at (Day 7B, Gate 7B-A) ---------------------------
// Raw unknown payloads are built independently of `validIncident`'s inferred
// TS shape so these tests exercise the runtime validator itself, not the
// compiler — a missing/malformed field here must be caught at runtime even
// if a caller's static type happened to be wrong or absent.

function rawValidIncidentPayload(): Record<string, unknown> {
  return {
    incident_id: "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
    fingerprint: "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
    device_id: "spine-01",
    source: "POLICY_VIOLATION",
    rule_ref: "policy-acl-external-in",
    affected_resource: "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
    severity: "Medium",
    status: "OPEN",
    evidence: {
      source_snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      violation_type: "MISSING_REQUIRED_ACL",
      expected_acl_name: "ACL-EXTERNAL-IN",
      actual_acl_name: null,
      interface_name: "GigabitEthernet0/1",
      direction: "in",
    },
    recommendation: "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    created_at: "2026-07-18T10:00:00Z",
    last_seen_at: "2026-07-18T10:00:00Z",
    occurrence_count: 1,
    updated_at: "2026-07-18T10:00:00Z",
    resolved_at: null,
  };
}

test("accepts updated_at plus resolved_at as a datetime string", async () => {
  const payload = {
    ...rawValidIncidentPayload(),
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
  };
  stubIncidentsResponse([payload]);

  const result = await fetchIncidents();

  expect(result).toEqual([payload]);
});

test("accepts updated_at plus resolved_at as null", async () => {
  const payload = rawValidIncidentPayload();
  stubIncidentsResponse([payload]);

  const result = await fetchIncidents();

  expect(result).toEqual([payload]);
});

test("rejects a payload missing updated_at", async () => {
  const payload = rawValidIncidentPayload();
  delete payload.updated_at;
  stubIncidentsResponse([payload]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects a payload missing the resolved_at key entirely", async () => {
  const payload = rawValidIncidentPayload();
  delete payload.resolved_at;
  stubIncidentsResponse([payload]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string updated_at", async () => {
  stubIncidentsResponse([{ ...rawValidIncidentPayload(), updated_at: "" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects an empty-string resolved_at", async () => {
  stubIncidentsResponse([{ ...rawValidIncidentPayload(), resolved_at: "" }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects resolved_at as a number", async () => {
  stubIncidentsResponse([{ ...rawValidIncidentPayload(), resolved_at: 1721300400 }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects resolved_at as an object", async () => {
  stubIncidentsResponse([{ ...rawValidIncidentPayload(), resolved_at: {} }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("rejects resolved_at as an array", async () => {
  stubIncidentsResponse([{ ...rawValidIncidentPayload(), resolved_at: [] }]);

  await expect(fetchIncidents()).rejects.toThrow(ApiRequestError);
});

test("a complete valid response retains every existing field plus updated_at and resolved_at", async () => {
  const payload = {
    ...rawValidIncidentPayload(),
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
  };
  stubIncidentsResponse([payload]);

  const result = await fetchIncidents();

  expect(result[0]).toEqual(payload);
  expect(Object.keys(result[0] ?? {}).sort()).toEqual(Object.keys(payload).sort());
});

test.each(["OPEN", "RESOLVED", "ACKNOWLEDGED", "SUPPRESSED"])(
  "remains structurally accepted for status %s (runtime validator stays forward-compatible)",
  async (status) => {
    const payload = {
      ...rawValidIncidentPayload(),
      status,
      resolved_at: status === "RESOLVED" ? "2026-07-18T11:00:00Z" : null,
    };
    stubIncidentsResponse([payload]);

    const result = await fetchIncidents();

    expect(result[0]?.status).toBe(status);
  },
);

// --- resolveIncident (Day 7B, Gate 7B-B) ------------------------------------

const RESOLVE_INCIDENT_ID = "8f14e45f-ceea-4c1d-8f1e-1234567890ab";

function rawResolvedIncidentPayload(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...rawValidIncidentPayload(),
    incident_id: RESOLVE_INCIDENT_ID,
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
    ...overrides,
  };
}

function stubResolveResponse(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, status));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// --- request shape -----------------------------------------------------

test("resolveIncident calls POST on the exact encoded path", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());

  await resolveIncident(RESOLVE_INCIDENT_ID);

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe(`http://localhost:8080/incidents/${RESOLVE_INCIDENT_ID}/resolve`);
  expect(init.method).toBe("POST");
});

test.each([
  ["with a space", "incident with space"],
  ["with a slash", "incident/with/slash"],
  ["with a quote", "incident\"with'quote"],
  ["with Unicode", "incident-üñîcode-😀"],
  ["with reserved characters", "incident?with=reserved&chars#here"],
])("resolveIncident safely encodes an incident ID %s", async (_label, rawId) => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload({ incident_id: rawId }));

  await resolveIncident(rawId);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe(`http://localhost:8080/incidents/${encodeURIComponent(rawId)}/resolve`);
});

test("resolveIncident sends no body key", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());

  await resolveIncident(RESOLVE_INCIDENT_ID);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect("body" in init).toBe(false);
});

test("resolveIncident sends Accept: application/json", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());

  await resolveIncident(RESOLVE_INCIDENT_ID);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
});

test("resolveIncident credentials is omit", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());

  await resolveIncident(RESOLVE_INCIDENT_ID);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.credentials).toBe("omit");
});

test("resolveIncident forwards an AbortSignal as the exact same object", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());
  const controller = new AbortController();

  await resolveIncident(RESOLVE_INCIDENT_ID, { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

// --- valid success -------------------------------------------------------

test("resolveIncident returns the complete RESOLVED IncidentResponse", async () => {
  const payload = rawResolvedIncidentPayload();
  stubResolveResponse(payload);

  const result = await resolveIncident(RESOLVE_INCIDENT_ID);

  expect(result).toEqual(payload);
  expect(result.status).toBe("RESOLVED");
});

test("resolveIncident preserves updated_at and resolved_at", async () => {
  const payload = rawResolvedIncidentPayload({
    updated_at: "2026-07-18T12:34:56Z",
    resolved_at: "2026-07-18T12:34:56Z",
  });
  stubResolveResponse(payload);

  const result = await resolveIncident(RESOLVE_INCIDENT_ID);

  expect(result.updated_at).toBe("2026-07-18T12:34:56Z");
  expect(result.resolved_at).toBe("2026-07-18T12:34:56Z");
});

test("resolveIncident preserves every existing incident field", async () => {
  const payload = rawResolvedIncidentPayload();
  stubResolveResponse(payload);

  const result = await resolveIncident(RESOLVE_INCIDENT_ID);

  expect(Object.keys(result).sort()).toEqual(Object.keys(payload).sort());
});

test("resolveIncident makes exactly one request and never calls GET /incidents", async () => {
  const fetchMock = stubResolveResponse(rawResolvedIncidentPayload());

  await resolveIncident(RESOLVE_INCIDENT_ID);

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.method).not.toBe("GET");
});

// --- invalid success -------------------------------------------------------

test("rejects a non-object success response", async () => {
  stubResolveResponse("not an object");

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response with a malformed required field", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ occurrence_count: "1" }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response whose incident_id differs from the requested ID", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ incident_id: "a-different-incident-id" }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response whose status is OPEN", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ status: "OPEN", resolved_at: null }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response whose status is ACKNOWLEDGED", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ status: "ACKNOWLEDGED" }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response whose status is an unknown non-empty string", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ status: "SUPPRESSED" }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

test("rejects a success response whose resolved_at is null", async () => {
  stubResolveResponse(rawResolvedIncidentPayload({ resolved_at: null }));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(ApiRequestError);
});

// --- failure ---------------------------------------------------------------

test("converts the exact incident_not_found response into an ApiRequestError", async () => {
  stubResolveResponse(
    { code: "incident_not_found", detail: "Incident 'missing-incident' was not found." },
    404,
  );

  try {
    await resolveIncident("missing-incident");
    expect.unreachable("expected resolveIncident to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("incident_not_found");
    expect((error as ApiRequestError).message).toBe("Incident 'missing-incident' was not found.");
  }
});

test("handles a malformed error response safely", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("not json", { status: 500, headers: { "Content-Type": "text/plain" } }),
      ),
  );

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("propagates a network failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

  await expect(resolveIncident(RESOLVE_INCIDENT_ID)).rejects.toThrow("Failed to fetch");
});

test("propagates an abort as an AbortError, unwrapped", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => {
      const error = new DOMException("The operation was aborted.", "AbortError");
      return Promise.reject(error);
    }),
  );
  const controller = new AbortController();

  try {
    await resolveIncident(RESOLVE_INCIDENT_ID, { signal: controller.signal });
    expect.unreachable("expected resolveIncident to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
  }
});
