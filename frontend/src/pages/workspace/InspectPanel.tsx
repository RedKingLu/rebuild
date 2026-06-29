/** Right-side inspection panel — Gate / Evidence / Trace / Audit / Context / Resource / Model. */
import { useState, useEffect } from 'react';
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
  projectId?: string;
}

export function InspectPanel({ tab, gates, activeGate, evidences, traces, audits, loading, projectId }: Props) {
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
      return <ContextPanel projectId={projectId} />;
    case 'resource':
      return <ResourcePanel />;
    case 'model':
      return <ModelPanel />;
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

// ── R9-3G-A: Real context / resource / model panels (replaces placeholders) ──

function ContextPanel({ projectId }: { projectId?: string }) {
  const [ctxData, setCtxData] = useState<any>(null);
  const [ctxLoading, setCtxLoading] = useState(true);
  const [ctxError, setCtxError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) { setCtxLoading(false); return; }
    setCtxLoading(true);
    fetch(`/api/projects/${projectId}/context`)
      .then(r => r.json())
      .then(d => { setCtxData(d?.data || d); setCtxLoading(false); })
      .catch(e => { setCtxError(e.message); setCtxLoading(false); });
  }, [projectId]);

  if (ctxLoading) return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>加载上下文…</div>;
  if (ctxError) return <div style={{ padding: 12, fontSize: 12, color: 'var(--red)' }}>上下文加载失败: {ctxError}</div>;

  const skills = (ctxData?.skills || []).filter((s: any) =>
    s?.category === 'common' || s?.category === ctxData?.current_stage
  );
  const project = ctxData?.project || {};

  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>上下文检视</b>

      {/* Project context */}
      <div style={{ marginTop: 8, padding: 8, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>项目信息</div>
        <div>名称: {project.name || '—'}</div>
        <div>来源: {project.source_type || '—'}</div>
        <div>状态: {project.workspace_status || '—'}</div>
        <div>引导: {project.onboarding_done ? '已完成' : '未完成'}</div>
        {project.coding_agent_ref && <div>编码Agent: {project.coding_agent_ref}</div>}
      </div>

      {/* Skills */}
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
          Skill 列表 ({skills.length})
        </div>
        {skills.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>暂无匹配 Skill</div>
        ) : (
          skills.slice(0, 20).map((s: any, i: number) => (
            <div key={i} style={{ marginTop: 4, padding: 4, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
              <div style={{ fontWeight: 600 }}>{s.name || s.skill_id || `Skill #${i + 1}`}</div>
              <div style={{ color: 'var(--color-text-muted)' }}>
                类别: {s.category || '—'} · 状态: {s.status || '—'}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ResourcePanel() {
  const [resources, setResources] = useState<any[]>([]);
  const [resLoading, setResLoading] = useState(true);
  const [resError, setResError] = useState<string | null>(null);

  useEffect(() => {
    setResLoading(true);
    fetch('/api/resources')
      .then(r => r.json())
      .then(d => { setResources(d?.data?.resources || d?.data || d?.resources || []); setResLoading(false); })
      .catch(e => { setResError(e.message); setResLoading(false); });
  }, []);

  if (resLoading) return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>加载资源…</div>;
  if (resError) return <div style={{ padding: 12, fontSize: 12, color: 'var(--red)' }}>资源加载失败: {resError}</div>;

  // Group by resource_type if available
  const grouped: Record<string, any[]> = {};
  for (const r of resources) {
    const t = r.resource_type || r.type || 'other';
    if (!grouped[t]) grouped[t] = [];
    grouped[t].push(r);
  }

  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>资源检视</b>
      <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
        共 {resources.length} 项资源
      </div>
      {resources.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 11, marginTop: 8 }}>暂无资源</div>
      ) : (
        Object.entries(grouped).map(([type, items]) => (
          <div key={type} style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, textTransform: 'capitalize' }}>
              {type} ({items.length})
            </div>
            {items.slice(0, 15).map((r: any, i: number) => (
              <div key={i} style={{ marginTop: 4, padding: 4, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
                <div style={{ fontWeight: 600 }}>{r.name || r.resource_id || `#${i + 1}`}</div>
                <div style={{ color: 'var(--color-text-muted)' }}>
                  {r.status || r.resource_status || '—'}
                  {r.risk_level && <> · 风险: {r.risk_level}</>}
                </div>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

function ModelPanel() {
  const [providers, setProviders] = useState<any[]>([]);
  const [gwStatus, setGwStatus] = useState<string>('loading');
  const [mdLoading, setMdLoading] = useState(true);
  const [mdError, setMdError] = useState<string | null>(null);

  useEffect(() => {
    setMdLoading(true);
    Promise.all([
      fetch('/api/model/providers').then(r => r.json()).catch(() => ({ data: { providers: [] } })),
      fetch('/api/model/status').then(r => r.json()).catch(() => ({ data: { overall_status: 'offline' } })),
    ]).then(([provResp, statusResp]) => {
      setProviders(provResp?.data?.providers || []);
      setGwStatus(statusResp?.data?.overall_status || statusResp?.data?.global_status || 'unknown');
      setMdLoading(false);
    }).catch(e => { setMdError(e.message); setMdLoading(false); });
  }, []);

  if (mdLoading) return <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>加载模型状态…</div>;
  if (mdError) return <div style={{ padding: 12, fontSize: 12, color: 'var(--red)' }}>模型加载失败: {mdError}</div>;

  const gwColor = gwStatus === 'healthy' ? 'var(--green)' : gwStatus === 'offline' ? 'var(--red)' : 'var(--amber)';

  return (
    <div style={{ fontSize: 12 }}>
      <b style={{ fontSize: 13 }}>模型检视</b>

      {/* Gateway status */}
      <div style={{ marginTop: 8, padding: 8, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>网关状态</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: gwColor, display: 'inline-block' }} />
          <span>{gwStatus}</span>
        </div>
        <div style={{ color: 'var(--color-text-muted)', marginTop: 4 }}>{providers.length} 个供应商</div>
      </div>

      {/* Provider list */}
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
          供应商 ({providers.length})
        </div>
        {providers.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>暂无已配置的模型供应商</div>
        ) : (
          providers.slice(0, 15).map((p: any, i: number) => (
            <div key={p.provider_id || i} style={{ marginTop: 4, padding: 4, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
              <div style={{ fontWeight: 600 }}>{p.provider_name || p.provider_id}</div>
              <div style={{ color: 'var(--color-text-muted)' }}>
                状态: {p.capability_marker || p.status || '—'}
                {p.model_count != null && <> · {p.model_count} 个模型</>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
