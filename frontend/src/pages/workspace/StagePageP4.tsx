/** StagePageP4 — R11-3-C8: P4 执行阶段页，展示执行进度 / 产出 / patch / Evidence / P4→P5 Gate。
 *  数据来自真实后端：
 *   - GET .../stages/{run}/stages/{stage}/taskgraph  → TaskGraph + 每节点执行状态(retry、node_status)
 *   - GET /api/projects/{id}/file?path=artifacts/p4_execution_summary.json → 变更清单 + patch 索引
 *   - GET /api/projects/{id}/file?path=output_code/|patches/|source/ → 真实产出 / diff / 源码(只读参考)
 *   - GET .../gates/active + GatePanel → P4→P5 晋级 Gate
 *  硬要求：中文优先 / Icon.tsx 线性图标(无 emoji) / 真实数据不挂 mock 横幅(D-049) / 真源只读
 *  确认 / No Evidence No Completed。 */
import { useState, useEffect } from 'react';
import { Icon, type IconKey } from '../../components/ui/Icon';
import { ModelUnavailableBanner, type ModelUnavailableInfo } from '../../components/ui/ModelUnavailableBanner';

interface Props {
  projectId: string;
  runId?: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

type GateInfo = { gate_id: string; gate_status: string; summary: string } | null;

const NODE_STATUS_STYLE: Record<string, { icon: IconKey; color: string; label: string }> = {
  completed: { icon: 'success', color: 'var(--green)', label: '完成' },
  running: { icon: 'run', color: 'var(--color-primary)', label: '执行中' },
  in_progress: { icon: 'run', color: 'var(--color-primary)', label: '执行中' },
  waiting_gate: { icon: 'gate', color: 'var(--amber)', label: '待决策' },
  blocked: { icon: 'blocked', color: 'var(--red)', label: '阻塞' },
  failed: { icon: 'error', color: 'var(--red)', label: '失败' },
  rework_required: { icon: 'refresh', color: 'var(--amber)', label: '需返工' },
  skipped: { icon: 'future', color: 'var(--color-text-muted)', label: '跳过' },
  pending: { icon: 'future', color: 'var(--color-text-muted)', label: '待执行' },
};

const card: React.CSSProperties = {
  padding: 14, background: 'var(--color-surface)',
  border: '1px solid var(--color-border)', borderRadius: 8,
};
const cardTitle: React.CSSProperties = { fontWeight: 600, fontSize: 14, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 };
const muted: React.CSSProperties = { fontSize: 12, color: 'var(--color-text-muted)' };

function NodeStatusIcon({ status }: { status: string }) {
  const s = NODE_STATUS_STYLE[status] || NODE_STATUS_STYLE.pending;
  return <Icon name={s.icon} size={14} style={{ color: s.color }} />;
}

function PreviewBody({ preview, path }: { preview: { path: string; content: string | null } | null; path: string }) {
  if (!preview || preview.path !== path) return null;
  return (
    <pre style={{ margin: 0, padding: 10, fontSize: 11, background: 'var(--color-surface)', borderTop: '1px solid var(--color-border)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 240, overflow: 'auto', fontFamily: 'var(--mono)' }}>
      {preview.content || '（空）'}
    </pre>
  );
}

export function StagePageP4({ projectId, runId = '', stageStatus, onReExecute }: Props) {
  const [tg, setTg] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);
  const [gate, setGate] = useState<GateInfo>(null);
  const [preview, setPreview] = useState<{ path: string; content: string | null } | null>(null);
  const [modelUnavailable, setModelUnavailable] = useState<ModelUnavailableInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      // Always read the real taskgraph (independent of model availability) → honest: empty
      // nodes, no fabrication when no run exists.
      const tgReq = runId || stageStatus
        ? fetch(`/api/projects/${projectId}/runs/${runId || ''}/stages/p4/taskgraph`)
        : Promise.resolve(null);
      const gateReq = fetch(`/api/projects/${projectId}/gates/active`);
      // WP-6 (EG-WP6-1): 拉取 P4 模型中断产物（artifacts/p4_model_error.json），驱动 ModelUnavailableBanner。
      const modelReq = fetch(`/api/projects/${projectId}/p4-summary`);
      const [tgRes, gateRes, modelRes] = await Promise.all([tgReq, gateReq, modelReq]);
      let tg = null;
      if (tgRes && tgRes.ok) tg = (await tgRes.json()).data || (await tgRes.json());
      setTg(tg);
      // C7 summary only exists AFTER a P4 execution ran; only fetch when a run is present
      // to avoid a 404 console error in the (honest) not-yet-executed case.
      if (tg && tg.graph_run_id) {
        const smRes = await fetch(
          `/api/projects/${projectId}/file?path=${encodeURIComponent('artifacts/p4_execution_summary.json')}`);
        if (smRes.ok) setSummary((await smRes.json()).data || (await smRes.json()));
      }
      if (gateRes.ok) {
        const g = (await gateRes.json()).data || (await gateRes.json());
        setGate(g && g.gate_id ? g : null);
      }
      if (modelRes.ok) {
        const md = (await modelRes.json()).data || (await modelRes.json());
        setModelUnavailable(md?.model_unavailable || null);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [projectId, runId]);

  const isGateOpen = gate && gate.gate_status === 'waiting_decision';
  const nodes: any[] = tg?.nodes || summary?.nodes || [];
  // ISSUE-04 修复：taskgraph 端点不返回 completed_node_count（见 routes_stages.get_taskgraph），
  // 旧版读 tg.completed_node_count 恒为 0；改为从真实节点状态统计。
  const completedNodeCount = nodes.filter((n: any) => n.status === 'completed').length;
  const changeManifest: any[] = summary?.change_manifest || [];
  const patchIndex: any[] = summary?.patch_index || [];
  const graphStatus = tg?.graph_status || summary?.graph_status;
  const statusColor =
    graphStatus === 'completed' ? 'var(--green)' : graphStatus === 'blocked' ? 'var(--red)' : 'var(--amber)';

  async function openPreview(path: string) {
    if (preview?.path === path) { setPreview(null); return; }
    try {
      const r = await fetch(`/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`);
      if (!r.ok) { setPreview({ path, content: `// 无法读取 (HTTP ${r.status})` }); return; }
      const d = await r.json();
      setPreview({ path, content: d?.data?.content ?? '' });
    } catch {
      setPreview({ path, content: '// 读取失败' });
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13 }}>
      {/* Header: stage status + executor badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, ...card }}>
        <Icon name="artifact" size={18} style={{ color: 'var(--color-primary)' }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>P4 执行</div>
          <div style={{ ...muted, marginTop: 2 }}>
            图状态 <b style={{ color: statusColor }}>{graphStatus || stageStatus || '未开始'}</b>
            {tg ? <> · {nodes.length} 节点 · 完成 {completedNodeCount}</> : null}
          </div>
        </div>
        {summary?.delegation || summary?.nodes?.some((n: any) => n.delegation) ? (
          <span style={{ fontSize: 10, color: 'var(--color-primary)', background: 'var(--color-primary-soft)', padding: '2px 8px', borderRadius: 10 }}>
            <Icon name="robot" size={11} /> 外部 Agent 执行
          </span>
        ) : (
          <span style={{ fontSize: 10, color: 'var(--color-text-muted)', background: 'var(--color-surface-subtle)', border: '1px solid var(--color-border)', padding: '2px 8px', borderRadius: 10 }}>
            平台内部执行
          </span>
        )}
      </div>

      {error && <div style={{ ...card, color: 'var(--red)' }}>加载失败: {error}</div>}

      {/* WP-6 (EG-WP6-1)：模型全失败强制中断 → 显式报错横幅(模型不可用/中断阶段/原因/链路/操作) */}
      {modelUnavailable && (
        <ModelUnavailableBanner info={modelUnavailable} onReExecute={onReExecute} />
      )}

      {/* P4→P5 Gate banner */}
      {isGateOpen && (
        <div style={{ ...card, background: 'var(--amber-soft, #fff8e1)', border: '1px solid var(--amber)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icon name="gate" size={16} style={{ color: 'var(--amber)' }} />
            <b>P4→P5 晋级 Gate：待决策</b>
            <span style={{ ...muted, marginLeft: 'auto' }}>{gate.gate_id?.slice(0, 8)}</span>
          </div>
          <div style={{ ...muted, marginTop: 4 }}>{gate.summary || '请完成 P4 执行后决策是否晋级 P5'}</div>
        </div>
      )}

      {loading && !tg && !summary && <div style={{ padding: 12, ...muted }}>加载 P4 执行结果…</div>}

      {/* 1. TaskGraph 执行进度(每节点) */}
      {nodes.length > 0 && (
        <div style={card}>
          <div style={cardTitle}><Icon name="trace" size={16} style={{ color: 'var(--color-primary)' }} /> 节点执行进度</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {nodes.map((n: any) => {
              const st = NODE_STATUS_STYLE[n.status] || NODE_STATUS_STYLE.pending;
              return (
                <div key={n.node_id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                  <NodeStatusIcon status={n.status} />
                  <span style={{ fontWeight: n.status === 'running' || n.status === 'in_progress' ? 600 : 400, color: st.color, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {n.title || n.node_id}
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{n.node_type}</span>
                  {n.retry_count > 0 && <span style={{ fontSize: 10, color: 'var(--amber)' }}>重试 {n.retry_count}</span>}
                  <span style={{ fontSize: 10, color: st.color, border: `1px solid ${st.color}`, borderRadius: 4, padding: '0 5px' }}>{st.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 2. 产出代码(只读,可预览) + 3. 变更清单 */}
      {changeManifest.length > 0 && (
        <div style={card}>
          <div style={cardTitle}><Icon name="files" size={16} style={{ color: 'var(--color-success)' }} /> 产出代码(只读，{changeManifest.length} 个文件)</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {changeManifest.map((e: any) => (
              <div key={e.path} style={{ border: '1px solid var(--color-border)', borderRadius: 6, overflow: 'hidden' }}>
                <div onClick={() => openPreview(e.path)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', cursor: 'pointer', background: 'var(--color-surface-subtle)' }}>
                  <Icon name="files" size={13} />
                  <code style={{ fontSize: 11, flex: 1 }}>{e.path}</code>
                  <span style={{ ...muted }}>{e.bytes}B · {e.sha256?.slice(0, 8)}…</span>
                </div>
                <PreviewBody preview={preview} path={e.path} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4. Patch 索引(diff) */}
      {patchIndex.length > 0 && (
        <div style={card}>
          <div style={cardTitle}><Icon name="git" size={16} style={{ color: 'var(--color-primary)' }} /> Patch 索引({patchIndex.length} 个 diff)</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {patchIndex.map((p: any) => (
              <div key={p.path} style={{ border: '1px solid var(--color-border)', borderRadius: 6, overflow: 'hidden' }}>
                <div onClick={() => openPreview(p.path)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', cursor: 'pointer', background: 'var(--color-surface-subtle)' }}>
                  <Icon name="git" size={13} />
                  <code style={{ fontSize: 11, flex: 1 }}>{p.path}</code>
                  {p.source_ref && <span style={{ ...muted }}>← {p.source_ref}</span>}
                  <span style={{ ...muted }}>{p.bytes}B</span>
                </div>
                <PreviewBody preview={preview} path={p.path} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evidence refs(来自 summary) */}
      {summary?.evidence_refs?.length > 0 && (
        <div style={card}>
          <div style={cardTitle}><Icon name="evidence" size={16} style={{ color: 'var(--color-primary)' }} /> Evidence({summary.evidence_refs.length})</div>
          <div style={{ ...muted, fontSize: 11 }}>{summary.evidence_refs.map((e: string) => <div key={e}>· {e}</div>)}</div>
        </div>
      )}

      {onReExecute && !isGateOpen && (
        <button className="btn sm" onClick={() => { onReExecute(); setTimeout(load, 600); }} style={{ alignSelf: 'flex-start' }}>
          <Icon name="refresh" size={12} /> 重新执行 P4
        </button>
      )}
    </div>
  );
}
