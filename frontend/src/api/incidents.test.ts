import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchIncidents } from "./incidents";
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
