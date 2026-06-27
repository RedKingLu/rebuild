/** Right-side inspection panel — Gate / Evidence / Trace / Audit / Context / Resource / Model. */
import type { Gate, Artifact, Evidence, Trace, Audit } from '../../types';

export type InspectTab = 'gate' | 'evidence' | 'trace' | 'audit' | 'context' | 'resource' | 'model';

interface Props {
  tab: InspectTab;
  gates: Gate[];
  activeGate: Gate | null;
  artifacts: Artifact[];
  evidences: Evidence[];
  traces: Trace[];
  audits: Audit[];
  loading: boolean;
}

export function InspectPanel({ tab, gates, activeGate, evidences, traces, audits, loading }: Props) {
  if (loading) return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>加载中…</div>;

  switch (tab) {
    case 'gate':
      return <GatePanel gates={gates} activeGate={activeGate} />;
    case 'evidence':
      return <EvidencePanel evidences={evidences} />;
    case 'trace':
      return <TracePanel traces={traces} />;
    case 'audit':
      return <AuditPanel audits={audits} />;
    case 'context':
      return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>上下文检视 — 将在后续阶段接入</div>;
    case 'resource':
      return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>资源检视 — 将在后续阶段接入</div>;
    case 'model':
      return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>模型检视 — 将在后续阶段接入</div>;
    default:
      return null;
  }
}

function GatePanel({ gates, activeGate }: { gates: Gate[]; activeGate: Gate | null }) {
  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>Gate 决策</b>
      {activeGate && (
        <div style={{ marginTop: 8, padding: 8, background: 'var(--amber)', borderRadius: 4 }}>
          <div style={{ fontWeight: 600 }}>⚠ 活跃 Gate</div>
          <div style={{ fontSize: 11 }}>{activeGate.gate_id} · {activeGate.gate_type} · {activeGate.risk_level || 'L0'}</div>
          <div style={{ fontSize: 11, marginTop: 4 }}>{activeGate.summary}</div>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>全部 Gate ({gates.length})</div>
        {gates.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>暂无 Gate 记录</div>
        ) : (
          gates.map(g => (
            <div key={g.gate_id} style={{ marginTop: 4, padding: 4, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>{g.gate_id}</span>
                <span className={`tag ${g.gate_status === 'waiting_decision' ? 'amber' : g.gate_status === 'approved' ? 'green' : ''}`}>{g.gate_status}</span>
              </div>
              <div style={{ color: 'var(--color-text-muted)' }}>{g.reason || g.summary}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function EvidencePanel({ evidences }: { evidences: Evidence[] }) {
  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>证据链</b>
      {evidences.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>暂无证据记录</div>
      ) : (
        evidences.map((e, i) => (
          <div key={i} style={{ marginTop: 6, padding: 6, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
            <div style={{ fontWeight: 600 }}>{e.evidence_id}</div>
            <div style={{ color: 'var(--color-text-muted)' }}>{e.summary}</div>
          </div>
        ))
      )}
    </div>
  );
}

function TracePanel({ traces }: { traces: Trace[] }) {
  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>过程追踪</b>
      {traces.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>暂无追踪记录</div>
      ) : (
        traces.slice(0, 30).map((t, i) => (
          <div key={i} style={{ marginTop: 4, padding: 4, borderBottom: '1px solid var(--color-border)', fontSize: 11 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 600 }}>{t.trace_type}</span>
              <span style={{ color: 'var(--color-text-muted)', fontSize: 10 }}>{t.created_at}</span>
            </div>
            <div>{(t as any).action || t.trace_type} · {t.summary}</div>
          </div>
        ))
      )}
    </div>
  );
}

function AuditPanel({ audits }: { audits: Audit[] }) {
  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>审计记录</b>
      {audits.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>暂无审计记录</div>
      ) : (
        audits.slice(0, 30).map((a, i) => (
          <div key={i} style={{ marginTop: 4, padding: 4, borderBottom: '1px solid var(--color-border)', fontSize: 11 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 600 }}>{(a as any).action || a.audit_type}</span>
              <span className={`tag ${a.decision === 'deny' ? 'red' : a.decision === 'executed' ? 'green' : ''}`}>{a.decision}</span>
            </div>
            <div style={{ color: 'var(--color-text-muted)' }}>{a.risk_level} · {(a as any).reason || ''}</div>
          </div>
        ))
      )}
    </div>
  );
}
