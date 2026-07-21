interface IncidentErrorStateProps {
  message: string;
  onRetry: () => void;
}

export function IncidentErrorState({ message, onRetry }: IncidentErrorStateProps) {
  return (
    <div className="status-message status-message--error" role="alert">
      <p>Unable to load incidents.</p>
      <p>{message}</p>
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
