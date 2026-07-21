import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { getJsonArray, postJson, postNoBody, ApiRequestError } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

test("joins the base URL and path without a double slash", async () => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080/");
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8080/incidents",
    expect.objectContaining({ method: "GET" }),
  );
});

test("joins with no trailing slash on the base URL", async () => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/incidents");
});

test("joins with multiple trailing slashes on the base URL", async () => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080///");
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/incidents");
});

test("defaults to http://localhost:8080 when VITE_API_BASE_URL is unset", async () => {
  vi.unstubAllEnvs();
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/incidents");
});

test("joins the endpoint as exactly /incidents", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/incidents");
});

test("uses GET and the correct endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://localhost:8080/incidents");
  expect(init.method).toBe("GET");
  expect(init.credentials).toBe("omit");
});

test("sends Accept: application/json", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
});

test("sends no Content-Type header on a GET request", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.has("Content-Type")).toBe(false);
});

test("sends no Authorization header", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.has("Authorization")).toBe(false);
});

test("credentials is omit", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  await getJsonArray("/incidents");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.credentials).toBe("omit");
});

test("returns a successful direct array response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([{ a: 1 }])));

  const result = await getJsonArray("/incidents");

  expect(result).toEqual([{ a: 1 }]);
});

test("passes an AbortSignal through as the exact same object", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  await getJsonArray("/incidents", { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

test("surfaces a {code, detail} error safely with the public detail as the message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500)),
  );

  await expect(getJsonArray("/incidents")).rejects.toThrow("DB down");
});

test("preserves the error code on the thrown ApiRequestError", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500)),
  );

  try {
    await getJsonArray("/incidents");
    expect.unreachable("expected getJsonArray to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("persistence_error");
    expect((error as ApiRequestError).message).toBe("DB down");
  }
});

test("produces a stable fallback message for a malformed (non-JSON) error body", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("not json", { status: 500, headers: { "Content-Type": "text/plain" } }),
      ),
  );

  await expect(getJsonArray("/incidents")).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("produces a stable fallback message for an empty error body", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 502 })));

  await expect(getJsonArray("/incidents")).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("produces a stable fallback message for an HTML error body, and never renders its content", async () => {
  const htmlBody =
    "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>Bad Gateway</h1></body></html>";
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(htmlBody, { status: 502, headers: { "Content-Type": "text/html" } }),
      ),
  );

  try {
    await getJsonArray("/incidents");
    expect.unreachable("expected getJsonArray to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    const message = (error as ApiRequestError).message;
    expect(message).toBe("The request failed and no further detail is available.");
    expect(message).not.toContain("<html>");
    expect(message).not.toContain("Bad Gateway");
  }
});

test("does not render raw JSON object text when the error body isn't {code, detail}-shaped", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ unexpected: "shape", nested: { a: 1 } }, 500)),
  );

  try {
    await getJsonArray("/incidents");
    expect.unreachable("expected getJsonArray to reject");
  } catch (error) {
    const message = (error as ApiRequestError).message;
    expect(message).toBe("The request failed and no further detail is available.");
    expect(message).not.toContain("unexpected");
    expect(message).not.toContain("nested");
  }
});

test("rejects a malformed successful payload rather than returning partial data", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ not: "an array" })));

  await expect(getJsonArray("/incidents")).rejects.toThrow(ApiRequestError);
});

test("uses the controlled incidents-response message for malformed 2xx JSON, not a parser exception", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{not valid json", { status: 200 })),
  );

  try {
    await getJsonArray("/incidents");
    expect.unreachable("expected getJsonArray to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    const message = (error as ApiRequestError).message;
    expect(message).toBe("The server returned a response that could not be understood.");
    // No parser implementation details (e.g. "SyntaxError", "JSON.parse",
    // "position") leak into the user-facing message.
    expect(message).not.toMatch(/SyntaxError|JSON\.parse|position \d+/i);
  }
});

test("passes an AbortSignal through when supplied", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  await getJsonArray("/incidents", { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

// ---------------------------------------------------------------------------
// postJson
// ---------------------------------------------------------------------------

test("postJson uses POST and the given path", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://localhost:8080/devices/spine-01/config");
  expect(init.method).toBe("POST");
});

test("postJson joins the base URL and path without a double slash", async () => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080/");
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe("http://localhost:8080/devices/spine-01/config");
});

test("postJson sends Accept: application/json", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
});

test("postJson sends Content-Type: application/json", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Content-Type")).toBe("application/json");
});

test("postJson sends no Authorization header", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.has("Authorization")).toBe(false);
});

test("postJson credentials is omit", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", { a: 1 });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.credentials).toBe("omit");
});

test("postJson sends the exact given body as JSON, nothing added or removed", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);

  await postJson("/devices/spine-01/config", {
    vendor: "cisco-ios-xe",
    raw_config_text: "hostname spine-01\n",
  });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(JSON.parse(init.body as string)).toEqual({
    vendor: "cisco-ios-xe",
    raw_config_text: "hostname spine-01\n",
  });
});

test("postJson passes an AbortSignal through as the exact same object", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 201));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  await postJson("/devices/spine-01/config", { a: 1 }, { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

test("postJson returns the parsed JSON body on a successful response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ device_id: "spine-01" }, 201)));

  const result = await postJson("/devices/spine-01/config", { a: 1 });

  expect(result).toEqual({ device_id: "spine-01" });
});

test("postJson surfaces a {code, detail} error safely with the public detail as the message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "device_conflict", detail: "conflict" }, 409)),
  );

  await expect(postJson("/devices/spine-01/config", { a: 1 })).rejects.toThrow("conflict");
});

test("postJson preserves the error code on the thrown ApiRequestError", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "device_conflict", detail: "conflict" }, 409)),
  );

  try {
    await postJson("/devices/spine-01/config", { a: 1 });
    expect.unreachable("expected postJson to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("device_conflict");
  }
});

test("postJson maps a FastAPI {detail: [...]} validation-error body to a stable safe message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "vendor"],
              msg: "field required",
              type: "value_error.missing",
            },
          ],
        },
        422,
      ),
    ),
  );

  try {
    await postJson("/devices/spine-01/config", { a: 1 });
    expect.unreachable("expected postJson to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    const message = (error as ApiRequestError).message;
    // Never the raw array, a validation location, rejected input, or the
    // literal field name/type internals.
    expect(message).not.toContain("loc");
    expect(message).not.toContain("vendor");
    expect(message).not.toContain("value_error.missing");
    expect(message).not.toMatch(/^\[/);
    expect(message.length).toBeGreaterThan(0);
  }
});

test("postJson produces a stable fallback message for a malformed (non-JSON) error body", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("not json", { status: 500, headers: { "Content-Type": "text/plain" } }),
      ),
  );

  await expect(postJson("/devices/spine-01/config", { a: 1 })).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("postJson produces a stable fallback message for an empty error body", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 502 })));

  await expect(postJson("/devices/spine-01/config", { a: 1 })).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("postJson produces a stable fallback message for an HTML error body, and never renders its content", async () => {
  const htmlBody =
    "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>Bad Gateway</h1></body></html>";
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(htmlBody, { status: 502, headers: { "Content-Type": "text/html" } }),
      ),
  );

  try {
    await postJson("/devices/spine-01/config", { a: 1 });
    expect.unreachable("expected postJson to reject");
  } catch (error) {
    const message = (error as ApiRequestError).message;
    expect(message).toBe("The request failed and no further detail is available.");
    expect(message).not.toContain("<html>");
    expect(message).not.toContain("Bad Gateway");
  }
});

test("postJson rejects an invalid-JSON successful response rather than throwing a raw parser error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{not valid json", { status: 201 })),
  );

  try {
    await postJson("/devices/spine-01/config", { a: 1 });
    expect.unreachable("expected postJson to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    const message = (error as ApiRequestError).message;
    expect(message).toBe("The server returned a response that could not be understood.");
    expect(message).not.toMatch(/SyntaxError|JSON\.parse|position \d+/i);
  }
});

test("postJson rejects an empty successful response body", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 201 })));

  await expect(postJson("/devices/spine-01/config", { a: 1 })).rejects.toThrow(ApiRequestError);
});

// ---------------------------------------------------------------------------
// postNoBody
// ---------------------------------------------------------------------------

test("postNoBody uses POST and the exact configured-base-URL path", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);

  await postNoBody("/incidents/inc-1/resolve");

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://localhost:8080/incidents/inc-1/resolve");
  expect(init.method).toBe("POST");
});

test("postNoBody sends Accept: application/json", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);

  await postNoBody("/incidents/inc-1/resolve");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
});

test("postNoBody credentials is omit", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);

  await postNoBody("/incidents/inc-1/resolve");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.credentials).toBe("omit");
});

test("postNoBody sends no body key in the RequestInit at all", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);

  await postNoBody("/incidents/inc-1/resolve");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect("body" in init).toBe(false);
});

test("postNoBody sends no Content-Type header", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);

  await postNoBody("/incidents/inc-1/resolve");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.has("Content-Type")).toBe(false);
});

test("postNoBody passes an AbortSignal through as the exact same object", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }, 200));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  await postNoBody("/incidents/inc-1/resolve", { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

test("postNoBody returns the parsed JSON body on a successful response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ incident_id: "inc-1" }, 200)));

  const result = await postNoBody("/incidents/inc-1/resolve");

  expect(result).toEqual({ incident_id: "inc-1" });
});

test("postNoBody surfaces a {code, detail} error using the existing ApiRequestError shape", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        jsonResponse(
          { code: "incident_not_found", detail: "Incident 'missing-incident' was not found." },
          404,
        ),
      ),
  );

  try {
    await postNoBody("/incidents/missing-incident/resolve");
    expect.unreachable("expected postNoBody to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("incident_not_found");
    expect((error as ApiRequestError).message).toBe("Incident 'missing-incident' was not found.");
  }
});

test("postNoBody produces the existing stable fallback message for a malformed (non-JSON) error body", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("not json", { status: 500, headers: { "Content-Type": "text/plain" } }),
      ),
  );

  await expect(postNoBody("/incidents/inc-1/resolve")).rejects.toThrow(
    "The request failed and no further detail is available.",
  );
});

test("postNoBody produces the existing stable fallback message for an HTML error body", async () => {
  const htmlBody =
    "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>Bad Gateway</h1></body></html>";
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(htmlBody, { status: 502, headers: { "Content-Type": "text/html" } }),
      ),
  );

  const message = await postNoBody("/incidents/inc-1/resolve").catch(
    (error: unknown) => (error as ApiRequestError).message,
  );
  expect(message).toBe("The request failed and no further detail is available.");
  expect(message).not.toContain("Bad Gateway");
});

test("postNoBody rejects an invalid-JSON successful response using the controlled malformed-response message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{not valid json", { status: 200 })),
  );

  try {
    await postNoBody("/incidents/inc-1/resolve");
    expect.unreachable("expected postNoBody to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).message).toBe(
      "The server returned a response that could not be understood.",
    );
  }
});

test("postNoBody propagates a network failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

  await expect(postNoBody("/incidents/inc-1/resolve")).rejects.toThrow("Failed to fetch");
});

test("postNoBody propagates an abort as an AbortError, unwrapped", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => {
      const error = new DOMException("The operation was aborted.", "AbortError");
      return Promise.reject(error);
    }),
  );
  const controller = new AbortController();

  try {
    await postNoBody("/incidents/inc-1/resolve", { signal: controller.signal });
    expect.unreachable("expected postNoBody to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
  }
});
