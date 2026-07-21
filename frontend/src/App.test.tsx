import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const STORAGE_KEY = "meta-rne-theme";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockSystemPreference(prefersDark: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-color-scheme: dark)" ? prefersDark : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])));
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

test("starts in dark mode when there is no stored preference and the OS prefers dark", () => {
  mockSystemPreference(true);
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("starts in light mode when there is no stored preference and the OS prefers light", () => {
  mockSystemPreference(false);
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe("light");
});

test("a stored dark preference overrides a light OS preference", () => {
  mockSystemPreference(false);
  window.localStorage.setItem(STORAGE_KEY, "dark");
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("a stored light preference overrides a dark OS preference", () => {
  mockSystemPreference(true);
  window.localStorage.setItem(STORAGE_KEY, "light");
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe("light");
});

test('in light mode, the toggle button is named "Switch to dark mode"', () => {
  mockSystemPreference(false);
  render(<App />);
  expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeInTheDocument();
});

test('in dark mode, the toggle button is named "Switch to light mode"', () => {
  mockSystemPreference(true);
  render(<App />);
  expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
});

test("clicking the toggle changes the root theme attribute", async () => {
  const user = userEvent.setup();
  mockSystemPreference(false);
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe("light");

  await user.click(screen.getByRole("button", { name: "Switch to dark mode" }));

  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
});

test("clicking the toggle persists the new choice in localStorage", async () => {
  const user = userEvent.setup();
  mockSystemPreference(false);
  render(<App />);

  await user.click(screen.getByRole("button", { name: "Switch to dark mode" }));

  expect(window.localStorage.getItem(STORAGE_KEY)).toBe("dark");
});

test("the product title and existing static descriptor remain rendered", () => {
  mockSystemPreference(false);
  render(<App />);
  expect(screen.getByText("Meta RNE Platform")).toBeInTheDocument();
  expect(
    screen.getByText("Multi-vendor configuration policy and incident operations"),
  ).toBeInTheDocument();
});

test("toggling the theme issues no additional fetch request", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  mockSystemPreference(false);
  render(<App />);

  const callCountBeforeToggle = fetchMock.mock.calls.length;
  await user.click(screen.getByRole("button", { name: "Switch to dark mode" }));

  expect(fetchMock.mock.calls.length).toBe(callCountBeforeToggle);
});
