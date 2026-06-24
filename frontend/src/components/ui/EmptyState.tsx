export function EmptyState({ msg, hint }: { msg: string; hint?: string }) {
  return (
    <div className="empty">
      <div style={{ fontSize: 14, marginBottom: 6 }}>{msg}</div>
      {hint && <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{hint}</div>}
    </div>
  );
}
