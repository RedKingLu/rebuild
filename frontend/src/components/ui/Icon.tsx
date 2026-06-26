/* Unified SVG icon component — VSCode Codicons-style linear icons, 27 keys per design pack */
import type { CSSProperties } from 'react';

export type IconKey =
  | 'overview' | 'project' | 'workspace' | 'stage' | 'run'
  | 'gate' | 'artifact' | 'evidence' | 'trace' | 'audit'
  | 'resource' | 'integration' | 'model' | 'fusion'
  | 'case' | 'knowledge' | 'community' | 'docs' | 'settings'
  | 'mock' | 'placeholder' | 'notConnected' | 'future' | 'blocked'
  | 'warning' | 'success' | 'error' | 'search' | 'files' | 'git'
  | 'remote' | 'agent' | 'chevronRight' | 'chevronLeft'
  | 'panelRight' | 'panelLeft' | 'panelBottom' | 'add' | 'externalLink'
  | 'robot' | 'delete' | 'key' | 'refresh' | 'edit' | 'import' | 'export' | 'send' | 'plug' | 'close';

interface Props {
  name: IconKey;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

export function Icon({ name, size = 20, className, style }: Props) {
  const s = size;
  const st: CSSProperties = { display: 'inline-block', flexShrink: 0, verticalAlign: 'middle', ...style };
  const A = ({d}: {d: string}) => <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />;

  const svg = (children: any, vb = '0 0 24 24') => (
    <svg width={s} height={s} viewBox={vb} fill="none" xmlns="http://www.w3.org/2000/svg" className={className} style={st} aria-hidden="true">
      {children}
    </svg>
  );

  switch (name) {
    // ── Navigation ──
    case 'overview':
      return svg(<><rect x="3" y="3" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.8"/><rect x="14" y="3" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.8"/><rect x="3" y="14" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.8"/><rect x="14" y="14" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'project':
      return svg(<><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-8l-2-2H5a2 2 0 00-2 2z" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'workspace':
      return svg(<><rect x="3" y="5" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M3 8h18" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="6" cy="6.5" r="0.8" fill="currentColor"/><circle cx="8.5" cy="6.5" r="0.8" fill="currentColor"/><circle cx="11" cy="6.5" r="0.8" fill="currentColor"/></>);
    case 'stage':
      return svg(<><circle cx="12" cy="6" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 8.5v3" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M9 15l3-3.5 3 3.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="7" cy="18" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="12" cy="18" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="17" cy="18" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'run':
      return svg(<><polygon points="7,4 19,12 7,20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/></>);
    case 'gate':
      return svg(<><path d="M12 2L3 7v6c0 5.25 3.75 10 9 12 5.25-2 9-6.75 9-12V7l-9-5z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'artifact':
      return svg(<><path d="M12 2L2 7l10 5 10-5-10-5z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M2 17l10 5 10-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M2 12l10 5 10-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/></>);
    case 'evidence':
      return svg(<><path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 2a10 10 0 100 20 10 10 0 000-20z" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'trace':
      return svg(<><path d="M6 18L18 6" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="9,6 18,6 18,15" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'audit':
      return svg(<><rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M9 12h6M9 16h6M9 8h3" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'resource':
      return svg(<><path d="M12 2l4 4-4 4-4-4 4-4z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M4 10l4 4-4 4-4-4 4-4z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M20 10l4 4-4 4-4-4 4-4z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/></>);
    case 'integration':
      return svg(<><path d="M10 2h4a2 2 0 012 2v5l3 3-3 3v5a2 2 0 01-2 2h-4" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="7" cy="6" r="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="7" cy="18" r="2" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'model':
      return svg(<><rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><rect x="8" y="8" width="8" height="8" rx="1" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 8v8M8 12h8" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'fusion':
      return svg(<><path d="M12 2L3 6v6c0 5.25 3.75 8.25 9 10 5.25-1.75 9-4.75 9-10V6l-9-4z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M8 10l2 2 4-4" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'case':
      return svg(<><path d="M4 19.5A2.5 2.5 0 016.5 17H20" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'knowledge':
      return svg(<><ellipse cx="12" cy="5" rx="8" ry="3" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'community':
      return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M8 12h8M12 8l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'docs':
      return svg(<><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="14,2 14,8 20,8" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M8 13h8M8 17h5" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'settings':
      return svg(<><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'search':
      return svg(<><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M16.5 16.5L21 21" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'files':
      return svg(<><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-8l-2-2H5a2 2 0 00-2 2z" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M9 12h6M9 16h3" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'git':
      return svg(<><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.7"/><circle cx="16" cy="16" r="2" fill="none" stroke="currentColor" strokeWidth="1.7"/><circle cx="16" cy="6" r="2" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="M8 8l3.5-1.5 3 1.5" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="M14 7.5v7l-4 2" fill="none" stroke="currentColor" strokeWidth="1.7"/></>);
    case 'remote':
      return svg(<><path d="M20 17.58A5 5 0 0018 8h-1.26A8 8 0 104 16.25" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="16,16 12,12 8,16" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 12v9" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'agent':
      return svg(<><rect x="4" y="3" width="16" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M8 21h8M12 16v5" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="12" cy="9" r="2" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    // ── Actions ──
    case 'chevronRight': return svg(<A d="M9 18l6-6-6-6"/>);
    case 'chevronLeft': return svg(<A d="M15 18l-6-6 6-6"/>);
    case 'panelRight': return svg(<><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M15 3v18" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'panelLeft': return svg(<><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M9 3v18" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'panelBottom': return svg(<><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M3 15h18" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'add': return svg(<><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'externalLink': return svg(<><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="15,3 21,3 21,9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M10 14L21 3" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    // ── Status ──
    case 'mock': return svg(<A d="M4 4l16 16M12 2a10 10 0 100 20 10 10 0 000-20z"/>);
    case 'placeholder': return svg(<><rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.8" strokeDasharray="4 3" fill="none"/></>);
    case 'notConnected': return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M4.93 4.93l14.14 14.14" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'future': return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="12,6 12,12 16,14" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'blocked': return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M6 6l12 12" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'warning': return svg(<><path d="M12 2L2 20h20L12 2z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M12 9v4M12 17h0" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'success': return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M8 12l3 3 5-5" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'error': return svg(<><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M15 9l-6 6M9 9l6 6" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    // ── R5-4 追加（线性风格，与图标库一致） ──
    case 'robot':
      return svg(<><rect x="4" y="8" width="16" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 4v4" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="12" cy="3.5" r="1.3" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="9" cy="13" r="1.2" fill="currentColor"/><circle cx="15" cy="13" r="1.2" fill="currentColor"/><path d="M9.5 16.5h5" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M2 12v3M22 12v3" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'delete':
      return svg(<><path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M10 11v6M14 11v6" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'key':
      return svg(<><circle cx="8" cy="8" r="4" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M11 11l9 9M17 17l2-2M20 14l-2 2" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'refresh':
      return svg(<><path d="M21 12a9 9 0 11-2.64-6.36M21 3v5h-5" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'edit':
      return svg(<><path d="M12 20h9" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4 12.5-12.5z" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'import':
      return svg(<><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="7,10 12,15 17,10" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 15V3" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'export':
      return svg(<><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" fill="none" stroke="currentColor" strokeWidth="1.8"/><polyline points="7,6 12,1 17,6" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M12 1v12" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'send':
      return svg(<><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/></>);
    case 'plug':
      return svg(<><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 01-10 0V8zM12 16v6" fill="none" stroke="currentColor" strokeWidth="1.8"/></>);
    case 'close':
      return svg(<A d="M18 6L6 18M6 6l12 12"/>);
    default: return svg(<circle cx="12" cy="12" r="3" fill="currentColor"/>);
  }
}
