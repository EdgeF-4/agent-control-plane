export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge ${status}`}>
      <span className="dot" />
      {status}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision: string }) {
  return (
    <span className={`badge ${decision}`}>
      <span className="dot" />
      {decision}
    </span>
  );
}
