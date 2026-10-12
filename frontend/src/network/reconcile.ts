import type { NetworkStateResponse } from "../api/networkStateTypes";

/**
 * Chooses which of two `GET /network/state` responses to keep. Compares the
 * parsed `collected_at` instants — never the strings — so an older HTTP
 * response can never overwrite a newer snapshot.
 *
 *  - incoming newer                       -> incoming
 *  - incoming older                       -> current (incoming ignored)
 *  - same instant, same collection_id     -> incoming (only `retrieved_at`
 *                                            can differ; the state is the same)
 *  - same instant, different collection_id-> current. The backend rejects this
 *                                            ambiguity, so fail conservatively.
 */
export function reconcileNetworkState(
  current: NetworkStateResponse | null,
  incoming: NetworkStateResponse,
): NetworkStateResponse {
  if (current === null) {
    return incoming;
  }
  const currentAt = Date.parse(current.collected_at);
  const incomingAt = Date.parse(incoming.collected_at);
  if (incomingAt > currentAt) {
    return incoming;
  }
  if (incomingAt < currentAt) {
    return current;
  }
  return incoming.collection_id === current.collection_id ? incoming : current;
}
