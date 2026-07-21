interface IncidentEmptyStateProps {
  onRefresh: () => void;
  isRefreshing: boolean;
}

export function IncidentEmptyState({ onRefresh, isRefreshing }: IncidentEmptyStateProps) {
  return (
    <div className="status-message" role="status" aria-live="polite" aria-busy={isRefreshing}>
      <p>No incidents detected.</p>
      <p>Incidents appear here after a submitted configuration violates policy.</p>
      <button type="button" onClick={onRefresh} disabled={isRefreshing}>
        Refresh
      </button>
    </div>
  );
}
