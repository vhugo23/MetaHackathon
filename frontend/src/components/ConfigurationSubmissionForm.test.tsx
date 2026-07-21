import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ConfigurationSubmissionForm } from "./ConfigurationSubmissionForm";
import { ApiRequestError } from "../api/client";
import * as configurationsModule from "../api/configurations";
import type { ConfigurationSubmissionResponse } from "../api/types";

vi.mock("../api/configurations", () => ({
  submitDeviceConfiguration: vi.fn(),
}));

const submitDeviceConfigurationMock = vi.mocked(configurationsModule.submitDeviceConfiguration);

const validResponse: ConfigurationSubmissionResponse = {
  device_id: "spine-01",
  snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  normalized_config: {
    hostname: "spine-01",
    interfaces: [],
    routing: { bgp_neighbors: [] },
    acls: [],
  },
  // Deliberately distinct so getByText can't collide across fields.
  violations_detected: 3,
  incidents_created: 2,
  incidents_updated: 1,
};

function fillValidForm(deviceId = "spine-01", rawConfigText = "hostname spine-01\n"): void {
  fireEvent.change(screen.getByLabelText(/device id/i), { target: { value: deviceId } });
  fireEvent.change(screen.getByLabelText(/raw configuration/i), {
    target: { value: rawConfigText },
  });
}

beforeEach(() => {
  submitDeviceConfigurationMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. Explicit labels
// ---------------------------------------------------------------------------

test("renders explicit labels for device ID, vendor, and raw configuration", () => {
  render(<ConfigurationSubmissionForm />);

  expect(screen.getByLabelText(/device id/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/vendor/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/raw configuration/i)).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 2. Vendor select
// ---------------------------------------------------------------------------

test("the vendor select is enabled and contains exactly one option: cisco-ios-xe / Cisco IOS-XE", () => {
  render(<ConfigurationSubmissionForm />);

  const select = screen.getByLabelText(/vendor/i);
  expect(select).toBeEnabled();

  const options = within(select).getAllByRole("option");
  expect(options).toHaveLength(1);
  expect(options[0]).toHaveValue("cisco-ios-xe");
  expect(options[0]).toHaveTextContent("Cisco IOS-XE");
});

// ---------------------------------------------------------------------------
// 3. Whitespace-only device ID rejected locally
// ---------------------------------------------------------------------------

test("a whitespace-only device ID is rejected locally with no POST issued", async () => {
  render(<ConfigurationSubmissionForm />);

  fireEvent.change(screen.getByLabelText(/device id/i), { target: { value: "   " } });
  fireEvent.change(screen.getByLabelText(/raw configuration/i), {
    target: { value: "hostname spine-01\n" },
  });
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(await screen.findByText("Enter a device ID.")).toBeInTheDocument();
  expect(screen.getByLabelText(/device id/i)).toHaveAttribute("aria-invalid", "true");
  expect(submitDeviceConfigurationMock).not.toHaveBeenCalled();
});

// ---------------------------------------------------------------------------
// 4. Empty raw configuration rejected locally
// ---------------------------------------------------------------------------

test("empty raw configuration text is rejected locally with no POST issued", async () => {
  render(<ConfigurationSubmissionForm />);

  fireEvent.change(screen.getByLabelText(/device id/i), { target: { value: "spine-01" } });
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(await screen.findByText("Enter configuration text.")).toBeInTheDocument();
  expect(screen.getByLabelText(/raw configuration/i)).toHaveAttribute("aria-invalid", "true");
  expect(submitDeviceConfigurationMock).not.toHaveBeenCalled();
});

// ---------------------------------------------------------------------------
// 5. Whitespace-only raw configuration is allowed locally
// ---------------------------------------------------------------------------

test("whitespace-only raw configuration text is allowed locally and passed unchanged", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "   ");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [, request] = submitDeviceConfigurationMock.mock.calls[0]!;
  expect(request.raw_config_text).toBe("   ");
});

// ---------------------------------------------------------------------------
// 6. Device ID whitespace preserved
// ---------------------------------------------------------------------------

test("a device ID with leading/trailing whitespace is passed unchanged", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);

  fillValidForm("  spine-01  ", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [deviceId] = submitDeviceConfigurationMock.mock.calls[0]!;
  expect(deviceId).toBe("  spine-01  ");
});

// ---------------------------------------------------------------------------
// 7. Raw configuration byte-exact preservation
// ---------------------------------------------------------------------------

test("raw configuration containing tabs, trailing spaces, and a final newline is passed unchanged", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);
  const rawConfigText = "hostname spine-01\n!\ttabbed\n!  trailing spaces  \n";

  fillValidForm("spine-01", rawConfigText);
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [, request] = submitDeviceConfigurationMock.mock.calls[0]!;
  expect(request.raw_config_text).toBe(rawConfigText);
});

test("a CRLF-containing value is preserved exactly as the textarea's own DOM value (browser-normalized line endings, not rewritten again by the component)", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);
  const textarea = screen.getByLabelText(/raw configuration/i);

  fireEvent.change(screen.getByLabelText(/device id/i), { target: { value: "spine-01" } });
  fireEvent.change(textarea, { target: { value: "hostname spine-01\r\n!\ttabbed\r\n" } });
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [, request] = submitDeviceConfigurationMock.mock.calls[0]!;
  // Whatever the DOM handed back as `.value` (a real <textarea> normalizes
  // CRLF to LF per the HTML spec, identically in real browsers and jsdom)
  // is passed through unchanged — the component never re-processes it.
  expect(request.raw_config_text).toBe((textarea as HTMLTextAreaElement).value);
});

// ---------------------------------------------------------------------------
// 8. Exact submit call shape
// ---------------------------------------------------------------------------

test("a valid submission calls submitDeviceConfiguration exactly once with the original device ID, request, and an AbortSignal", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [deviceId, request, options] = submitDeviceConfigurationMock.mock.calls[0]!;
  expect(deviceId).toBe("spine-01");
  expect(request).toEqual({ vendor: "cisco-ios-xe", raw_config_text: "hostname spine-01\n" });
  expect(options?.signal).toBeInstanceOf(AbortSignal);
});

// ---------------------------------------------------------------------------
// 9. Pending presentation
// ---------------------------------------------------------------------------

test("shows a pending status, marks the form busy, and natively disables the submit button while submitting", () => {
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  const { container } = render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(screen.getByRole("status")).toHaveTextContent("Submitting configuration…");
  expect(container.querySelector("form")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("button", { name: /submit configuration/i })).toBeDisabled();
});

// ---------------------------------------------------------------------------
// 10. Second click while disabled
// ---------------------------------------------------------------------------

test("a second attempted click while the submit button is disabled produces no second POST", async () => {
  const user = userEvent.setup();
  submitDeviceConfigurationMock.mockReturnValue(new Promise(() => {}));
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  const submitButton = screen.getByRole("button", { name: /submit configuration/i });
  fireEvent.click(submitButton);

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  expect(submitButton).toBeDisabled();

  // userEvent respects the native `disabled` attribute and will not dispatch
  // a click to a disabled button, mirroring real browser/user behavior.
  await user.click(submitButton);

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
});

// ---------------------------------------------------------------------------
// 11. ApiRequestError presentation
// ---------------------------------------------------------------------------

test("an ApiRequestError produces a controlled role=alert message and its optional code", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(
    new ApiRequestError("vendor not recognized", "unsupported_vendor"),
  );
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("vendor not recognized");
  expect(alert).toHaveTextContent("unsupported_vendor");
});

// ---------------------------------------------------------------------------
// 12. Network / unexpected failure presentation
// ---------------------------------------------------------------------------

test("an unexpected/network failure displays only the hook's stable safe message", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(new TypeError("Failed to fetch: secret detail"));
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).not.toHaveTextContent("secret detail");
  expect(alert).not.toHaveTextContent("Failed to fetch");
  expect(alert.textContent?.length ?? 0).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// 13. Success fields
// ---------------------------------------------------------------------------

test("a successful response renders device_id, snapshot_id, violations_detected, incidents_created, and incidents_updated", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  expect(screen.getByText(validResponse.device_id)).toBeInTheDocument();
  expect(screen.getByText(validResponse.snapshot_id)).toBeInTheDocument();
  expect(screen.getByText(String(validResponse.violations_detected))).toBeInTheDocument();
  expect(screen.getByText(String(validResponse.incidents_created))).toBeInTheDocument();
  expect(screen.getByText(String(validResponse.incidents_updated))).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 14. Success role=status
// ---------------------------------------------------------------------------

test("success presentation uses role=status", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  // A transient role="status" pending region also exists momentarily, so
  // wait for the success confirmation text itself, then check the region
  // that actually carries it — not just "any role=status appears".
  const confirmation = await screen.findByText(/configuration submitted successfully/i);
  expect(confirmation.closest('[role="status"]')).not.toBeNull();
});

// ---------------------------------------------------------------------------
// 15 / 16. normalized_config presentation
// ---------------------------------------------------------------------------

test("normalized_config is exposed inside a semantic details/summary region", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  const summary = screen.getByText(/normalized configuration/i);
  expect(summary.closest("details")).not.toBeNull();
});

test("normalized configuration is rendered as escaped text, not raw HTML", async () => {
  const responseWithHtmlLikeHostname: ConfigurationSubmissionResponse = {
    ...validResponse,
    normalized_config: {
      ...validResponse.normalized_config,
      hostname: "<img src=x onerror=alert(1)>",
    },
  };
  submitDeviceConfigurationMock.mockResolvedValue(responseWithHtmlLikeHostname);
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  const summary = screen.getByText(/normalized configuration/i);
  const details = summary.closest("details") as HTMLElement;
  expect(details.querySelector("img")).toBeNull();
  expect(within(details).getByText(/img src=x onerror=alert\(1\)/)).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 17 / 18. onSubmissionSuccess
// ---------------------------------------------------------------------------

test("onSubmissionSuccess is called exactly once for a successful submission", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  const onSubmissionSuccess = vi.fn();
  render(<ConfigurationSubmissionForm onSubmissionSuccess={onSubmissionSuccess} />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await waitFor(() => {
    expect(onSubmissionSuccess).toHaveBeenCalledTimes(1);
  });
  expect(onSubmissionSuccess).toHaveBeenCalledWith(validResponse);
});

test("onSubmissionSuccess is not called for a local validation failure", async () => {
  const onSubmissionSuccess = vi.fn();
  render(<ConfigurationSubmissionForm onSubmissionSuccess={onSubmissionSuccess} />);

  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(await screen.findByText("Enter a device ID.")).toBeInTheDocument();
  expect(submitDeviceConfigurationMock).not.toHaveBeenCalled();
  expect(onSubmissionSuccess).not.toHaveBeenCalled();
});

test("onSubmissionSuccess is not called for a failed POST", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(new ApiRequestError("bad", "invalid_request"));
  const onSubmissionSuccess = vi.fn();
  render(<ConfigurationSubmissionForm onSubmissionSuccess={onSubmissionSuccess} />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByRole("alert");
  expect(onSubmissionSuccess).not.toHaveBeenCalled();
});

// ---------------------------------------------------------------------------
// 19. Values remain after success
// ---------------------------------------------------------------------------

test("form values remain present after a successful submission", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  render(<ConfigurationSubmissionForm />);

  fillValidForm("spine-01", "hostname spine-01\n");
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  expect(screen.getByLabelText(/device id/i)).toHaveValue("spine-01");
  expect(screen.getByLabelText(/raw configuration/i)).toHaveValue("hostname spine-01\n");
});

// ---------------------------------------------------------------------------
// 20. Editing an invalid field clears its message
// ---------------------------------------------------------------------------

test("editing an invalid field clears its local validation message without rewriting the input", async () => {
  render(<ConfigurationSubmissionForm />);

  fireEvent.change(screen.getByLabelText(/raw configuration/i), {
    target: { value: "hostname spine-01\n" },
  });
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  const deviceIdInput = screen.getByLabelText(/device id/i);
  expect(await screen.findByText("Enter a device ID.")).toBeInTheDocument();
  expect(deviceIdInput).toHaveAttribute("aria-invalid", "true");

  fireEvent.change(deviceIdInput, { target: { value: "spine-01" } });

  expect(screen.queryByText("Enter a device ID.")).not.toBeInTheDocument();
  expect(deviceIdInput).not.toHaveAttribute("aria-invalid");
  expect(deviceIdInput).toHaveValue("spine-01");
});
