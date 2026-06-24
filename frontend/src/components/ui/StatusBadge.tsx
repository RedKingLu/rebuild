import type { MockLevel } from '../../types';

type BadgeTone = 'green' | 'amber' | 'red' | 'violet' | 'orange' | 'blue' | 'grey';

function toneForMock(level: MockLevel): BadgeTone {
  if (level === 'mock') return 'violet';
  if (level === 'not_connected') return 'orange';
  if (level === 'placeholder' || level === 'future') return 'grey';
  return 'grey';
}

export function StatusBadge({ label, tone, className = '' }: { label: string; tone?: BadgeTone; className?: string }) {
  return <span className={`tag ${tone || 'grey'} ${className}`}>{label}</span>;
}

export function MockBadge({ level, className = '' }: { level: MockLevel; className?: string }) {
  const labels: Record<MockLevel, string> = { mock: 'Mock', placeholder: '占位', not_connected: '未接服务', future: '规划中' };
  const t = toneForMock(level);
  return <span className={`tag ${t} ${className}`}>{labels[level]}</span>;
}

export function EvidenceBadge({ status, validation }: { status: string; validation: string }) {
  const isGap = status === 'insufficient' || validation === 'validation_failed' || validation === 'validation_blocked';
  const isCandidate = status === 'candidate' && validation === 'not_validated';
  if (isGap) return <span className="tag red">证据缺口</span>;
  if (isCandidate) return <span className="tag amber">证据候选</span>;
  if (validation === 'validation_passed') return <span className="tag green">已验证</span>;
  return <span className="tag grey">{status}</span>;
}
