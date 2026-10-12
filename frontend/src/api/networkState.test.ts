import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ApiRequestError, MALFORMED_RESPONSE_MESSAGE } from "./client";
import { fetchNetworkState, isOperationalStateNotFound } from "./networkState";
import { isNetworkStateResponse } from "./networkStateTypes";
import {
  BASELINE_NETWORK_STATE,
  DEGRADED_NETWORK_STATE,
  allBgpUnavailableState,
  cloneState,
  jsonResponse,
  notFoundNoSnapshotResponse,
} from "../test/networkFixtures";

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function stubFetch(response: Response | Promise<Response>) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the promise to reject");
}

describe("fetchNetworkState", () => {
  test("accepts the valid baseline response and issues a plain credential-less GET", async () => {
    const fetchMock = stubFetch(jsonResponse(BASELINE_NETWORK_STATE));
    await expect(fetchNetworkState()).resolves.toEqual(BASELINE_NETWORK_STATE);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8080/network/state",
      expect.objectContaining({ method: "GET", credentials: "omit" }),
    );
  });

  test("accepts the valid degraded response (real Active states)", async () => {
    stubFetch(jsonResponse(DEGRADED_NETWORK_STATE));
    const state = await fetchNetworkState();
    const leaf1 = state.nodes.find((n) => n.node_id === "leaf-1")!;
    expect(leaf1.bgp!.neighbors.map((n) => n.state)).toEqual(["Active", "Established"]);
  });

  test("accepts a valid UNKNOWN / unavailable response", async () => {
    const unknownNeighbor = cloneState(BASELINE_NETWORK_STATE);
    const leaf1 = unknownNeighbor.nodes.find((n) => n.node_id === "leaf-1")!;
    leaf1.bgp!.neighbors[0] = {
      ...leaf1.bgp!.neighbors[0]!,
      state: "Unknown",
      raw_state: "Clearing",
      prefixes_received: null,
      uptime_ms: null,
    };
    stubFetch(jsonResponse(unknownNeighbor));
    await expect(fetchNetworkState()).resolves.toEqual(unknownNeighbor);

    stubFetch(jsonResponse(allBgpUnavailableState()));
    const unavailable = await fetchNetworkState();
    expect(unavailable.nodes.find((n) => n.node_id === "spine-1")!.bgp).toBeNull();
  });

  test("passes the abort signal through to fetch", async () => {
    const fetchMock = stubFetch(jsonResponse(BASELINE_NETWORK_STATE));
    const controller = new AbortController();
    await fetchNetworkState({ signal: controller.signal });
    expect((fetchMock.mock.calls[0]![1] as RequestInit).signal).toBe(controller.signal);
  });

  test("preserves the 404 operational_state_not_found code", async () => {
    stubFetch(notFoundNoSnapshotResponse());
    const error = await rejection(fetchNetworkState());
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("operational_state_not_found");
    expect(isOperationalStateNotFound(error)).toBe(true);
  });

  test("another error code is not the no-snapshot case", async () => {
    stubFetch(jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500));
    const error = await rejection(fetchNetworkState());
    expect((error as ApiRequestError).code).toBe("persistence_error");
    expect(isOperationalStateNotFound(error)).toBe(false);
  });

  test.each([
    ["an array", []],
    ["null", null],
    ["a string", "state"],
    ["an empty object", {}],
  ])("rejects %s with the stable malformed-response message", async (_name, body) => {
    stubFetch(jsonResponse(body));
    const error = await rejection(fetchNetworkState());
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).message).toBe(MALFORMED_RESPONSE_MESSAGE);
    expect((error as ApiRequestError).code).toBeUndefined();
  });

  test("rejects an unparseable body", async () => {
    stubFetch(new Response("<html>oops</html>", { status: 200 }));
    const error = await rejection(fetchNetworkState());
    expect((error as ApiRequestError).message).toBe(MALFORMED_RESPONSE_MESSAGE);
  });
});

describe("isNetworkStateResponse (nested validation, no coercion)", () => {
  function mutated(change: (state: ReturnType<typeof cloneState>) => void) {
    const copy = cloneState(BASELINE_NETWORK_STATE);
    change(copy);
    return copy;
  }

  test("the fixtures are valid", () => {
    expect(isNetworkStateResponse(BASELINE_NETWORK_STATE)).toBe(true);
    expect(isNetworkStateResponse(DEGRADED_NETWORK_STATE)).toBe(true);
  });

  test.each<[string, (s: ReturnType<typeof cloneState>) => void]>([
    ["missing collection_id", (s) => Reflect.deleteProperty(s, "collection_id")],
    ["unparseable collected_at", (s) => (s.collected_at = "not-a-date")],
    ["unparseable retrieved_at", (s) => (s.retrieved_at = "")],
    ["missing source.lab_name", (s) => Reflect.deleteProperty(s.source, "lab_name")],
    ["nodes not an array", (s) => ((s as unknown as Record<string, unknown>).nodes = {})],
    [
      "unknown node role",
      (s) => ((s.nodes[0] as unknown as Record<string, unknown>).role = "switch"),
    ],
    [
      "interface with an unknown oper_state",
      (s) => ((s.nodes[2]!.interfaces![0] as unknown as Record<string, unknown>).oper_state = "UP"),
    ],
    [
      "interface addresses not strings",
      (s) => ((s.nodes[2]!.interfaces![0] as unknown as Record<string, unknown>).addresses = [1]),
    ],
    [
      "BGP neighbor with an unknown state string",
      (s) =>
        ((s.nodes[2]!.bgp!.neighbors[0] as unknown as Record<string, unknown>).state =
          "established"),
    ],
    [
      "BGP neighbor remote_as as a string",
      (s) =>
        ((s.nodes[2]!.bgp!.neighbors[0] as unknown as Record<string, unknown>).remote_as = "65000"),
    ],
    ["negative uptime", (s) => (s.nodes[2]!.bgp!.neighbors[0]!.uptime_ms = -1)],
    ["BGP missing router_id", (s) => Reflect.deleteProperty(s.nodes[2]!.bgp!, "router_id")],
    [
      "route ecmp_path_count not an integer",
      (s) => ((s.nodes[2]!.routes![0] as unknown as Record<string, unknown>).ecmp_path_count = 1.5),
    ],
    [
      "next hop with a numeric ip",
      (s) => ((s.nodes[2]!.routes![0]!.next_hops[0] as unknown as Record<string, unknown>).ip = 7),
    ],
    [
      "reachability succeeded not boolean",
      (s) =>
        ((s.nodes[0]!.reachability![0] as unknown as Record<string, unknown>).succeeded = "true"),
    ],
    [
      "reachability received missing",
      (s) => Reflect.deleteProperty(s.nodes[0]!.reachability![0]!, "received"),
    ],
    [
      "unavailable entry with an unknown facet",
      (s) => (s.nodes[0]!.unavailable = [{ facet: "cpu" as never, reason: "x" }]),
    ],
    [
      "unavailable entry without a reason",
      (s) => (s.nodes[0]!.unavailable = [{ facet: "bgp", reason: "" }]),
    ],
    ["unavailable missing", (s) => Reflect.deleteProperty(s.nodes[0]!, "unavailable")],
  ])("rejects: %s", (_name, change) => {
    expect(isNetworkStateResponse(mutated(change))).toBe(false);
  });

  test("enum strings are matched exactly, never coerced", () => {
    const state = mutated((s) => {
      (s.nodes[2]!.bgp!.neighbors[0] as unknown as Record<string, unknown>).state = "ESTABLISHED";
    });
    expect(isNetworkStateResponse(state)).toBe(false);
  });
});
