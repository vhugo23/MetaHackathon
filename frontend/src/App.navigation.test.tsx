import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { BASELINE_NETWORK_STATE, jsonResponse } from "./test/networkFixtures";

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      const path = new URL(url).pathname;
      return Promise.resolve(
        path === "/network/state" ? jsonResponse(BASELINE_NETWORK_STATE) : jsonResponse([]),
      );
    }),
  );
  window.location.hash = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.location.hash = "";
});

test("the live network workspace is the default view", async () => {
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Live Network", level: 1 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Live Network" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.queryByRole("heading", { name: "Network Incidents" })).toBeNull();
});

test("existing incident, configuration and telemetry tools stay reachable via the nav", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("link", { name: "Incidents & Tools" }));
  expect(
    await screen.findByRole("heading", { name: "Network Incidents", level: 1 }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Submit device configuration", level: 2 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Incidents & Tools" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.queryByRole("heading", { name: "Live Network" })).toBeNull();
});

test("the view follows the URL hash, in both directions", async () => {
  window.location.hash = "#tools";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Network Incidents", level: 1 }),
  ).toBeInTheDocument();

  act(() => {
    window.location.hash = "#network";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(
    await screen.findByRole("heading", { name: "Live Network", level: 1 }),
  ).toBeInTheDocument();
});

test("the tools view does not poll", () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    window.location.hash = "#tools";
    render(<App />);
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
