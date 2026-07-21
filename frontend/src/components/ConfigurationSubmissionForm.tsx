import { useState, type ChangeEvent, type FormEvent } from "react";
import { useConfigurationSubmission } from "../hooks/useConfigurationSubmission";
import type { ConfigurationSubmissionResponse } from "../api/types";

const DEVICE_ID_ERROR_MESSAGE = "Enter a device ID.";
const RAW_CONFIG_ERROR_MESSAGE = "Enter configuration text.";

interface ConfigurationSubmissionFormProps {
  onSubmissionSuccess?: (response: ConfigurationSubmissionResponse) => void | Promise<void>;
}

export function ConfigurationSubmissionForm({
  onSubmissionSuccess,
}: ConfigurationSubmissionFormProps) {
  const { state, submit } = useConfigurationSubmission({ onSuccess: onSubmissionSuccess });

  const [deviceId, setDeviceId] = useState("");
  const [rawConfigText, setRawConfigText] = useState("");
  const [deviceIdError, setDeviceIdError] = useState<string | null>(null);
  const [rawConfigError, setRawConfigError] = useState<string | null>(null);

  const isSubmitting = state.status === "submitting";

  function handleDeviceIdChange(event: ChangeEvent<HTMLInputElement>) {
    const value = event.target.value;
    setDeviceId(value);
    if (deviceIdError && value.trim().length > 0) {
      setDeviceIdError(null);
    }
  }

  function handleRawConfigChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    setRawConfigText(value);
    if (rawConfigError && value.length > 0) {
      setRawConfigError(null);
    }
  }

  function handleVendorChange(event: ChangeEvent<HTMLSelectElement>) {
    // Only one vendor is currently supported — this select exists to make
    // that explicit, not to offer a real choice, so there is nothing to
    // store beyond the fixed value already rendered.
    void event;
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // Defensive guard beyond the native `disabled` submit button — a form
    // submit event (e.g. via Enter) can in principle still fire even when
    // the submit control itself is disabled.
    if (isSubmitting) {
      return;
    }

    const deviceIdBlank = deviceId.trim().length === 0;
    const rawConfigEmpty = rawConfigText.length === 0;

    setDeviceIdError(deviceIdBlank ? DEVICE_ID_ERROR_MESSAGE : null);
    setRawConfigError(rawConfigEmpty ? RAW_CONFIG_ERROR_MESSAGE : null);

    if (deviceIdBlank || rawConfigEmpty) {
      return;
    }

    submit(deviceId, { vendor: "cisco-ios-xe", raw_config_text: rawConfigText });
  }

  return (
    <section className="configuration-submission">
      <h2>Submit device configuration</h2>

      <form
        className="configuration-submission__form"
        onSubmit={handleSubmit}
        aria-busy={isSubmitting}
        noValidate
      >
        <div className="form-field">
          <label htmlFor="configuration-device-id">Device ID</label>
          <input
            id="configuration-device-id"
            type="text"
            value={deviceId}
            onChange={handleDeviceIdChange}
            aria-invalid={deviceIdError ? "true" : undefined}
            aria-describedby={deviceIdError ? "configuration-device-id-error" : undefined}
          />
          {deviceIdError && (
            <p id="configuration-device-id-error" className="form-field__error" role="alert">
              {deviceIdError}
            </p>
          )}
        </div>

        <div className="form-field">
          <label htmlFor="configuration-vendor">Vendor</label>
          <select id="configuration-vendor" value="cisco-ios-xe" onChange={handleVendorChange}>
            <option value="cisco-ios-xe">Cisco IOS-XE</option>
          </select>
        </div>

        <div className="form-field">
          <label htmlFor="configuration-raw-config-text">Raw configuration</label>
          <textarea
            id="configuration-raw-config-text"
            className="configuration-submission__textarea"
            value={rawConfigText}
            onChange={handleRawConfigChange}
            rows={12}
            aria-invalid={rawConfigError ? "true" : undefined}
            aria-describedby={rawConfigError ? "configuration-raw-config-text-error" : undefined}
          />
          {rawConfigError && (
            <p id="configuration-raw-config-text-error" className="form-field__error" role="alert">
              {rawConfigError}
            </p>
          )}
        </div>

        <button type="submit" disabled={isSubmitting}>
          Submit configuration
        </button>

        {isSubmitting && (
          <p role="status" className="configuration-submission__pending">
            Submitting configuration…
          </p>
        )}
      </form>

      {state.status === "error" && (
        <div className="status-message status-message--error" role="alert">
          <p>{state.message}</p>
          {state.code && <p className="configuration-submission__error-code">Code: {state.code}</p>}
        </div>
      )}

      {state.status === "success" && (
        <div className="status-message status-message--success" role="status">
          <p>Configuration submitted successfully.</p>
          <dl className="configuration-submission__result-fields">
            <div>
              <dt>Device</dt>
              <dd>{state.response.device_id}</dd>
            </div>
            <div>
              <dt>Snapshot</dt>
              <dd>{state.response.snapshot_id}</dd>
            </div>
            <div>
              <dt>Violations detected</dt>
              <dd>{state.response.violations_detected}</dd>
            </div>
            <div>
              <dt>Incidents created</dt>
              <dd>{state.response.incidents_created}</dd>
            </div>
            <div>
              <dt>Incidents updated</dt>
              <dd>{state.response.incidents_updated}</dd>
            </div>
          </dl>
          <details>
            <summary>Normalized configuration</summary>
            <pre className="configuration-submission__normalized-config">
              {JSON.stringify(state.response.normalized_config, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
