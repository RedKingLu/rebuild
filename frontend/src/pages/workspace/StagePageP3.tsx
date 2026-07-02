/** StagePageP3 — R10 T19: P3 规划阶段页，展示契约 §5.5 的核心产出。
 *  数据来自 GET /api/projects/{id}/planning-summary（读 DB：StagePlan + TaskPlan(Batch) +
 *  TaskGraph 的 nodes/edges）。三块：Stage Plan / Task Plan(Batch) / TaskGraph（DAG 简化视图）。
 *  硬要求：中文优先 / 线性图标 Icon.tsx（无 emoji）/ 真实数据不挂 mock 横幅(D-049) /
 *  高风险与 Gate 必生可见(D-023) / model id 非 Key（脱敏）。
 */
import { useState, useEffect } from 'react';
import { Icon } from '../../components/ui/Icon';
import { RISK_LABELS } from '../../services/resourceService';

interface Props {
  projectId: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

const RISK_COLOR: Record<string, string> = {
  L0: 'var(--color-text-muted)', L1: 'var(--color-text-muted)',
  L2: 'var(--color-primary)', L3: 'var(--color-warning)',
  L4: 'var(--color-warning-strong)', L5: 'var(--color-danger)',
};

// 边类型 → 中文标签 + 语义色（§8.1 十种边类型）
const EDGE_LABEL: Record<string, string> = {
  sequence: '串行', parallel: '并行', conditional: '条件', branch: '分支',
  merge: '合并', failure: '失败', retry: '重试', rework: '返工',
  loop: '循环', nested: '嵌套',
};
const EDGE_COLOR: Record<string, string> = {
  sequence: 'var(--color-primary)', parallel: 'var(--color-success)',
  conditional: 'var(--color-warning)', branch: 'var(--color-warning)',
  merge: 'var(--color-success)', failure: 'var(--color-danger)',
  retry: 'var(--color-warning-strong)', rework: 'var(--color-warning-strong)',
  loop: 'var(--color-mock)', nested: 'var(--color-text-muted)',
};

const card: React.CSSProperties = {
  padding: 14, background: 'var(--color-surface)',
  border: '1px solid var(--color-border)', borderRadius: 8,
};
const cardTitle: React.CSSProperties = { fontWeight: 600, fontSize: 14, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 };
const muted: React.CSSProperties = { fontSize: 12, color: 'var(--color-text-muted)' };

function RiskBadge({ level }: { level: string }) {
  const c = RISK_COLOR[level] || 'var(--color-text-muted)';
  return (
    <span style={{ fontSize: 10, fontWeight: 600, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 5px', whiteSpace: 'nowrap' }}>
      {RISK_LABELS[level] || level}
    </span>
  );
}

export function StagePageP3({ projectId, stageStatus, onReExecute }: Props) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isChangesRequested = stageStatus === 'changes_requested';
  const isBlocked = stageStatus === 'blocked';

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/projects/${projectId}/planning-summary`)
      .then(r => r.json())
      .then(d => { setData(d?.data || d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [projectId]);

  if (loading) return <div style={{ padding: 12, ...muted }}>加载 P3 规划结果…</div>;
  if (error) return <div style={{ padding: 12, fontSize: 13, color: 'var(--red)' }}>加载失败：{error}</div>;

  const available = data?.available !== false;
  const sp = data?.stage_plan || {};
  const batch = data?.task_batch || {};
  const taskPlans: any[] = data?.task_plans || [];
  const tg = data?.task_graph || null;
  const modelUsed: string | null = data?.model_used || null;
  const scope: string[] = sp?.scope?.scope || [];
  const outOfScope: string[] = sp?.scope?.out_of_scope || [];
  const nodeById: Record<string, any> = {};
  (tg?.nodes || []).forEach((n: any) => { nodeById[n.node_id] = n; });

  return (
    <div style={{ fontSize: 13 }}>
      <h3 style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="stage" size={18} /> P3 规划 — 方案 · 计划 · TaskGraph
      </h3>

      {isChangesRequested && (
        <div style={{ padding: '10px 14px', marginBottom: 12, background: 'var(--orange-bg)', border: '1px solid var(--orange)', borderRadius: 6, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="warning" size={16} style={{ color: 'var(--orange)' }} />
          <span style={{ flex: 1 }}>阶段需要返工。请依据 Gate 决策原因修订方案/计划/TaskGraph 后重新规划。</span>
          {onReExecute && <button className="btn sm" style={{ background: 'var(--orange)', color: '#fff' }} onClick={onReExecute}>重新执行</button>}
        </div>
      )}
      {isBlocked && (
        <div style={{ padding: '10px 14px', marginBottom: 12, background: 'var(--red-bg)', border: '1px solid var(--red)', borderRadius: 6, fontSize: 12, color: 'var(--red)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="blocked" size={16} />
          <span>阶段已被阻塞。P3 规划依赖有效模型（无 Key 不降级为规则规划），请确认模型配置或查看 Gate 决策原因。</span>
        </div>
      )}

      {!available && (
        <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="future" size={18} style={{ color: 'var(--color-text-muted)' }} />
          <span style={muted}>{data?.reason || 'P3 规划尚未执行。请先完成 P2 评估并批准 Gate 以触发 P3 规划。'}</span>
        </div>
      )}

      {available && (
        <>
          {modelUsed && (
            <div style={{ padding: '9px 13px', marginBottom: 12, background: 'var(--color-primary-soft)', border: '1px solid var(--color-primary-border)', borderRadius: 6, fontSize: 12, color: 'var(--color-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="model" size={15} />
              <span style={{ flex: 1 }}>
                本规划为 LLM 生成的<strong>待审计划草案</strong>，须经 P3→P4 用户 Gate 审核批准后方可执行。
                （规划模型：<code style={{ fontFamily: 'var(--font-mono)' }}>{modelUsed}</code>）
              </span>
            </div>
          )}

          {/* 1. Stage Plan */}
          <div style={{ ...card, marginBottom: 12 }}>
            <div style={cardTitle}>
              <Icon name="docs" size={16} /> Stage Plan（阶段计划）
              <RiskBadge level={sp.risk_level || 'L0'} />
              <span style={{ ...muted, fontSize: 11 }}>状态：{sp.plan_status || '—'} · {sp.stage_plan_id}</span>
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.7 }}>
              <div style={{ marginBottom: 6 }}><span style={{ fontWeight: 600 }}>阶段目标：</span>{sp.objective || <span style={muted}>（未提供）</span>}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginTop: 8 }}>
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>范围内</div>
                  {scope.length ? scope.map((s, i) => <div key={i} style={{ padding: '2px 0' }}>· {s}</div>) : <div style={muted}>（未声明）</div>}
                </div>
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>明确不做（out_of_scope）</div>
                  {outOfScope.length ? outOfScope.map((s, i) => <div key={i} style={{ padding: '2px 0', color: 'var(--color-text-muted)' }}>· {s}</div>) : <div style={muted}>（未声明）</div>}
                </div>
              </div>
              {sp.permission_boundary && <div style={{ marginTop: 8 }}><span style={{ fontWeight: 600 }}>权限边界：</span>{sp.permission_boundary}</div>}
              {sp.validation_strategy && <div style={{ marginTop: 4 }}><span style={{ fontWeight: 600 }}>P5 验证策略：</span>{sp.validation_strategy}</div>}
              {Array.isArray(sp.completion_criteria) && sp.completion_criteria.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>完成条件</div>
                  {sp.completion_criteria.map((c: any, i: number) => <div key={i} style={{ padding: '2px 0' }}>· {typeof c === 'string' ? c : JSON.stringify(c)}</div>)}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
            {/* 2. Task Plan Batch */}
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="artifact" size={16} /> Task Plan（任务计划 · 批次 {batch.task_count || 0}）
                <RiskBadge level={batch.batch_risk_level || 'L0'} />
              </div>
              {batch.gate_required && (
                <div style={{ fontSize: 11, color: 'var(--color-danger)', display: 'flex', alignItems: 'center', gap: 5, marginBottom: 8 }}>
                  <Icon name="gate" size={14} /> 批次含高风险任务 → 需 P3→P4 用户 Gate 批准
                </div>
              )}
              {batch.batch_objective && <div style={{ fontSize: 12, marginBottom: 8, color: 'var(--color-text-muted)' }}>{batch.batch_objective}</div>}
              {taskPlans.length === 0 ? <div style={muted}>无任务计划。</div> : (
                <div style={{ fontSize: 12 }}>
                  {taskPlans.map((t, i) => (
                    <div key={t.task_plan_id} style={{ padding: '5px 0', borderBottom: '1px solid var(--color-border)', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <span style={{ color: 'var(--color-text-muted)', minWidth: 18 }}>{i + 1}.</span>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          <span style={{ flex: 1 }}>{t.title || t.objective}</span>
                          <RiskBadge level={t.risk_level || 'L0'} />
                        </div>
                        {t.validation_method && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>验证：{t.validation_method}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 3. TaskGraph — DAG 简化视图 */}
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="trace" size={16} /> TaskGraph（DAG 简化视图）
                {tg?.degraded === true && (
                  <span style={{ fontSize: 10, color: 'var(--color-warning)', border: '1px solid var(--color-warning)', borderRadius: 4, padding: '1px 5px' }}>最简退化·单链</span>
                )}
              </div>
              {!tg ? <div style={muted}>无 TaskGraph（Q-R10-3：P3 必生，若缺请检查规划）。</div> : (
                <>
                  <div style={{ ...muted, fontSize: 11, marginBottom: 8 }}>
                    {tg.node_count} 节点 · {tg.edge_count} 边 · 状态 {tg.graph_status} · {tg.task_graph_id}
                  </div>
                  {/* 节点分层（生成顺序）+ 串行主干箭头 */}
                  <div style={{ marginBottom: 10 }}>
                    {(tg.nodes || []).map((n: any, i: number) => (
                      <div key={n.node_id}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '5px 8px', background: 'var(--color-surface-subtle)', borderRadius: 6, borderLeft: `3px solid ${RISK_COLOR[n.risk_level] || 'var(--color-border)'}` }}>
                          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)' }}>{i + 1}</span>
                          <span style={{ flex: 1, fontSize: 12 }}>{n.title}</span>
                          <RiskBadge level={n.risk_level || 'L0'} />
                        </div>
                        {i < (tg.nodes || []).length - 1 && (
                          <div style={{ textAlign: 'center', color: 'var(--color-text-faint)', fontSize: 12, lineHeight: '14px' }}>↓</div>
                        )}
                      </div>
                    ))}
                  </div>
                  {/* 边策略清单（显式，§5.7-3） */}
                  {(tg.edges || []).length > 0 && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>边策略（依赖关系）</div>
                      {tg.edges.map((e: any, i: number) => {
                        const sTitle = nodeById[e.source_node_id]?.title || e.source_node_id;
                        const tTitle = nodeById[e.target_node_id]?.title || e.target_node_id;
                        const c = EDGE_COLOR[e.edge_type] || 'var(--color-text-muted)';
                        return (
                          <div key={i} style={{ fontSize: 11, padding: '2px 0', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ color: c, fontWeight: 600, minWidth: 44 }}>{EDGE_LABEL[e.edge_type] || e.edge_type}</span>
                            <span style={{ color: 'var(--color-text-muted)' }}>{sTitle} → {tTitle}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
