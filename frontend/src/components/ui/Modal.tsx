/** Generic Modal overlay — R9-3G-B.
 *  Pattern: position:fixed backdrop + centered card with stopPropagation.
 *  Mirrors AddProviderModal.tsx lines 94-101.
 */
import type { ReactNode } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  width?: number;
  children: ReactNode;
}

export function Modal({ open, onClose, title, width, children }: Props) {
  if (!open) return null;

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1200,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div className="card" style={{
        width: `min(${width || 640}px, 96vw)`,
        maxHeight: '90vh', overflow: 'auto', padding: 24,
        background: 'var(--color-surface)', borderRadius: 12,
        border: '1px solid var(--color-border)',
      }} onClick={e => e.stopPropagation()}>
        {title && (
          <div className="spread" style={{ marginBottom: 16 }}>
            <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
            <button className="btn sm ghost" onClick={onClose} aria-label="关闭" style={{ fontSize: 18, lineHeight: 1 }}>✕</button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
