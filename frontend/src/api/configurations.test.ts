import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { submitDeviceConfiguration } from "./configurations";
import { ApiRequestError } from "./client";
import type { ConfigurationSubmissionRequest, ConfigurationSubmissionResponse } from "./types";

function jsonResponse(body: unknown, status = 201): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const validInterface = {
  name: "GigabitEthernet0/1",
  description: null,
  ip_address: "10.0.0.1/30",
  mtu: null,
  admin_state: "up",
  acl_in: null,
  acl_out: null,
};

const validAclEntry = {
  sequence: 10,
  action: "permit",
  protocol: "tcp",
  source: "any",
  destination: "any",
};

const validAcl = {
  name: "ACL-EXTERNAL-IN",
  entries: [validAclEntry],
};

const validNormalizedConfig = {
  hostname: "spine-01",
  interfaces: [validInterface],
  routing: { bgp_neighbors: [{ neighbor_ip: "10.0.0.2", remote_as: 65001 }] },
  acls: [validAcl],
};

const validSubmissionResponse: ConfigurationSubmissionResponse = {
  device_id: "spine-01",
  snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  normalized_config: validNormalizedConfig,
  violations_detected: 1,
  incidents_created: 1,
  incidents_updated: 0,
};

const validRequest: ConfigurationSubmissionRequest = {
  vendor: "cisco-ios-xe",
  raw_config_text: "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n",
};

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function stubResponse(body: unknown, status = 201): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, status));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// ---------------------------------------------------------------------------
// Path-segment encoding
// ---------------------------------------------------------------------------

test("encodes the device ID as one URL path segment", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("spine-01", validRequest);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/devices/spine-01/config");
});

test("encodes a device ID containing a slash as a single opaque path segment", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("rack/spine-01", validRequest);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/devices/rack%2Fspine-01/config");
});

test("encodes a device ID containing spaces and reserved characters", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("spine 01?#&", validRequest);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe(`http://localhost:8080/devices/${encodeURIComponent("spine 01?#&")}/config`);
});

test("does not trim or otherwise rewrite the device ID used to build the path", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("  spine-01  ", validRequest);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe(`http://localhost:8080/devices/${encodeURIComponent("  spine-01  ")}/config`);
});

// ---------------------------------------------------------------------------
// Exact request object
// ---------------------------------------------------------------------------

test("uses POST with the exact required headers and credentials", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("spine-01", validRequest);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.method).toBe("POST");
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
  expect(headers.get("Content-Type")).toBe("application/json");
  expect(init.credentials).toBe("omit");
});

test("forwards an AbortSignal as the exact same object", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);
  const controller = new AbortController();

  await submitDeviceConfiguration("spine-01", validRequest, { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

test("sends exactly {vendor, raw_config_text} in the body, preserved byte-for-byte", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);
  const rawConfigText = "hostname spine-01\r\n!  trailing spaces  \n\ttabbed\n";

  await submitDeviceConfiguration("spine-01", {
    vendor: "cisco-ios-xe",
    raw_config_text: rawConfigText,
  });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string) as Record<string, unknown>;
  expect(body).toEqual({ vendor: "cisco-ios-xe", raw_config_text: rawConfigText });
  expect(Object.keys(body).sort()).toEqual(["raw_config_text", "vendor"]);
});

test("sends the widened arista-eos vendor value unchanged (Gate 8A-E)", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);
  const rawConfigText = "hostname leaf-02\n!\ninterface Ethernet1\n   ip address 10.0.1.1/30\n!\n";

  await submitDeviceConfiguration("leaf-02", {
    vendor: "arista-eos",
    raw_config_text: rawConfigText,
  });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string) as Record<string, unknown>;
  expect(body).toEqual({ vendor: "arista-eos", raw_config_text: rawConfigText });
});

// ---------------------------------------------------------------------------
// no device_id or observed_at in the body
// ---------------------------------------------------------------------------

test("never includes device_id in the request body", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("spine-01", validRequest);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string) as Record<string, unknown>;
  expect(body).not.toHaveProperty("device_id");
});

test("never includes observed_at in the request body", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  await submitDeviceConfiguration("spine-01", validRequest);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string) as Record<string, unknown>;
  expect(body).not.toHaveProperty("observed_at");
});

test("strips extra runtime properties from a caller-supplied request object, even though the type forbids them", async () => {
  const fetchMock = stubResponse(validSubmissionResponse);

  // TypeScript's structural typing does not guarantee a runtime object has
  // only the declared keys. This deliberately widens the runtime value
  // beyond ConfigurationSubmissionRequest via an intersection type — a
  // test-only technique to represent what an untrusted/misbehaving caller
  // could actually pass at runtime — without weakening the production
  // request type itself.
  const requestWithExtraProperties: ConfigurationSubmissionRequest & {
    device_id: string;
    observed_at: string;
    arbitrary_extra: number;
  } = {
    vendor: "cisco-ios-xe",
    raw_config_text: "hostname spine-01\n",
    device_id: "spine-01",
    observed_at: "2026-07-18T10:00:00Z",
    arbitrary_extra: 42,
  };

  await submitDeviceConfiguration("spine-01", requestWithExtraProperties);

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string) as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(["raw_config_text", "vendor"]);
  expect(body).toEqual({
    vendor: "cisco-ios-xe",
    raw_config_text: "hostname spine-01\n",
  });
});

// ---------------------------------------------------------------------------
// Complete success-response structural validation
// ---------------------------------------------------------------------------

test("returns the validated response when the payload matches the contract exactly", async () => {
  stubResponse(validSubmissionResponse);

  const result = await submitDeviceConfiguration("spine-01", validRequest);

  expect(result).toEqual(validSubmissionResponse);
});

test("accepts a response with an empty interfaces/acls/bgp_neighbors set", async () => {
  const minimal: ConfigurationSubmissionResponse = {
    device_id: "spine-01",
    snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    normalized_config: {
      hostname: "spine-01",
      interfaces: [],
      routing: { bgp_neighbors: [] },
      acls: [],
    },
    violations_detected: 0,
    incidents_created: 0,
    incidents_updated: 0,
  };
  stubResponse(minimal);

  const result = await submitDeviceConfiguration("spine-01", validRequest);

  expect(result).toEqual(minimal);
});

test.each([
  ["device_id", { ...validSubmissionResponse, device_id: "" }],
  ["snapshot_id", { ...validSubmissionResponse, snapshot_id: "" }],
  ["violations_detected as string", { ...validSubmissionResponse, violations_detected: "1" }],
  ["violations_detected negative", { ...validSubmissionResponse, violations_detected: -1 }],
  ["violations_detected non-integer", { ...validSubmissionResponse, violations_detected: 1.5 }],
  ["incidents_created negative", { ...validSubmissionResponse, incidents_created: -1 }],
  ["incidents_updated negative", { ...validSubmissionResponse, incidents_updated: -1 }],
])("rejects a top-level malformed field: %s", async (_label, malformed) => {
  stubResponse(malformed);

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

// ---------------------------------------------------------------------------
// Structurally malformed but valid JSON response rejection
// ---------------------------------------------------------------------------

test("rejects a response missing normalized_config entirely", async () => {
  const { normalized_config, ...withoutNormalizedConfig } = validSubmissionResponse;
  void normalized_config;
  stubResponse(withoutNormalizedConfig);

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("rejects a top-level array instead of an object", async () => {
  stubResponse([validSubmissionResponse]);

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("accepts a response containing an extraneous static_routes field without treating it specially", async () => {
  // An unknown extra field must not cause rejection of an otherwise-valid
  // shape, and its presence must not be relied on for anything — the
  // frontend adds no static_routes field of its own anywhere in this
  // module.
  const withExtraField = {
    ...validSubmissionResponse,
    normalized_config: {
      ...validSubmissionResponse.normalized_config,
      routing: { bgp_neighbors: [], static_routes: [] },
    },
  };
  stubResponse(withExtraField);

  const result = await submitDeviceConfiguration("spine-01", validRequest);

  expect(result.normalized_config.routing.bgp_neighbors).toEqual([]);
});

// ---------------------------------------------------------------------------
// Nested normalized_config validation
// ---------------------------------------------------------------------------

test("rejects a malformed interface (missing name)", async () => {
  const { name, ...withoutName } = validInterface;
  void name;
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: { ...validNormalizedConfig, interfaces: [withoutName] },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("rejects an interface with a wrong-typed mtu", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      interfaces: [{ ...validInterface, mtu: "1500" }],
    },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("accepts an interface with a non-null integer mtu", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      interfaces: [{ ...validInterface, mtu: 1500 }],
    },
  });

  const result = await submitDeviceConfiguration("spine-01", validRequest);

  expect(result.normalized_config.interfaces[0]?.mtu).toBe(1500);
});

test("rejects a malformed bgp neighbor (non-integer remote_as)", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      routing: { bgp_neighbors: [{ neighbor_ip: "10.0.0.2", remote_as: "65001" }] },
    },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("rejects a malformed acl (entries not an array)", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      acls: [{ name: "ACL-EXTERNAL-IN", entries: "not-an-array" }],
    },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("rejects a malformed acl entry (missing action)", async () => {
  const { action, ...withoutAction } = validAclEntry;
  void action;
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      acls: [{ name: "ACL-EXTERNAL-IN", entries: [withoutAction] }],
    },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("rejects an acl entry with a non-integer sequence", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      acls: [{ name: "ACL-EXTERNAL-IN", entries: [{ ...validAclEntry, sequence: "10" }] }],
    },
  });

  await expect(submitDeviceConfiguration("spine-01", validRequest)).rejects.toThrow(
    ApiRequestError,
  );
});

test("accepts a null description/ip_address/acl_in/acl_out on an interface", async () => {
  stubResponse({
    ...validSubmissionResponse,
    normalized_config: {
      ...validNormalizedConfig,
      interfaces: [
        {
          name: "GigabitEthernet0/1",
          description: null,
          ip_address: null,
          mtu: null,
          admin_state: "up",
          acl_in: null,
          acl_out: null,
        },
      ],
    },
  });

  const result = await submitDeviceConfiguration("spine-01", validRequest);

  expect(result.normalized_config.interfaces[0]).toEqual({
    name: "GigabitEthernet0/1",
    description: null,
    ip_address: null,
    mtu: null,
    admin_state: "up",
    acl_in: null,
    acl_out: null,
  });
});

// ---------------------------------------------------------------------------
// Error propagation from the underlying client (thin wrapper proof)
// ---------------------------------------------------------------------------

test("propagates a controlled {code, detail} error unchanged", async () => {
  stubResponse({ code: "unsupported_vendor", detail: "vendor not recognized" }, 422);

  try {
    await submitDeviceConfiguration("spine-01", validRequest);
    expect.unreachable("expected submitDeviceConfiguration to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("unsupported_vendor");
    expect((error as ApiRequestError).message).toBe("vendor not recognized");
  }
});
