import { describe, expect, test } from "vitest";
import { BASELINE_NETWORK_STATE } from "../test/networkFixtures";
import { reconcileNetworkState } from "./reconcile";

const current = BASELINE_NETWORK_STATE; // collected_at 2026-10-07T12:00:00Z

function at(collectedAt: string, collectionId: string, retrievedAt = "2026-10-07T13:00:00Z") {
  return {
    ...BASELINE_NETWORK_STATE,
    collected_at: collectedAt,
    collection_id: collectionId,
    retrieved_at: retrievedAt,
  };
}

describe("reconcileNetworkState", () => {
  test("the first response is accepted", () => {
    expect(reconcileNetworkState(null, current)).toBe(current);
  });

  test("a newer collected_at wins", () => {
    const newer = at("2026-10-07T12:00:01Z", "newer");
    expect(reconcileNetworkState(current, newer)).toBe(newer);
  });

  test("an older collected_at is ignored", () => {
    const older = at("2026-10-07T11:59:59Z", "older");
    expect(reconcileNetworkState(current, older)).toBe(current);
  });

  test("compares instants, not strings (differently formatted offsets)", () => {
    // Lexicographically "2026-10-07T13:00:00+02:00" > "2026-10-07T12:00:00Z",
    // but it is the instant 11:00:00Z, i.e. older.
    const older = at("2026-10-07T13:00:00+02:00", "older-offset");
    expect(reconcileNetworkState(current, older)).toBe(current);
    const newer = at("2026-10-07T14:30:00+02:00", "newer-offset"); // 12:30Z
    expect(reconcileNetworkState(current, newer)).toBe(newer);
  });

  test("the same collection refreshes retrieval metadata", () => {
    const same = at("2026-10-07T12:00:00Z", current.collection_id, "2026-10-07T12:00:30Z");
    expect(reconcileNetworkState(current, same)).toBe(same);
  });

  test("equal collected_at with a different collection_id keeps the current state", () => {
    const ambiguous = at("2026-10-07T12:00:00Z", "someone-else");
    expect(reconcileNetworkState(current, ambiguous)).toBe(current);
  });
});
