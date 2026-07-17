/** StagePageP5 — R12-3-C7: P5 验证阶段页，展示全量验证结果 + 真实命令输出 + P5→P6 Gate。
 *  数据来自真实后端：
 *   - GET /api/projects/{id}/runs/{run_id}/p5/input → 完整 P5InputFacts + validation_plan + verify_results
 *   - GET /api/projects/{id}/gates/active + GatePanel → P5→P6 晋级 Gate
 *
 *  硬要求：
 *   - 中文优先 / Icon.tsx 线性图标(无 emoji) / 真实数据不挂 mock 横幅(D-049)
 *   - candidate / validated / failed / evidence_gap / needs_user_input 必须视觉区分
 *   - No Evidence No Completed / D-105① 全量真实命令验证
 *   - P5 完成后创建 P5→P6 Gate；存在未接受 Evidence Gap 不得创建通过 Gate
 */
import { useState, useEffect } from 'react';
import { Icon, type IconKey } from '../../components/ui/Icon';

interface Props {
  projectId: string;
  runId?: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

type GateInfo = { gate_id: string; gate_status: string; summary: string; stage?: string } | null;

/* ── 槽位状态 视觉映射（D-105① / D-049 必须视觉区分）── */
const SLOT_STATUS_STYLE: Record<string, { icon: IconKey; color: string; label: string }> = {
  validated:         { icon: 'success',  color: 'var(--green)',            label: '已通过' },
  validation_failed: { icon: 'error',    color: 'var(--red)',              label: '验证失败' },
  evidence_gap:      { icon: 'evidence', color: 'var(--amber)',            label: '证据缺失' },
  needs_user_input:  { icon: 'gate',     color: 'var(--purple, #8b5cf6)',  label: '需用户输入' },
  pending:           { icon: 'future',   color: 'var(--color-text-muted)', label: '待验证' },
  in_progress:       { icon: 'run',      color: 'var(--color-primary)',    label: '验证中' },
  superseded:        { icon: 'future',   color: 'var(--color-text-muted)', label: '已替代' },
  not_applicable:    { icon: 'future',   color: 'var(--color-text-muted)', label: '不适用' },
};

const SLOT_TYPE_LABEL: Record<string, string> = {
  hard_required: '硬必需',
  conditional:   '有条件必需',
  enhanced:      '增强项',
};

const card: React.CSSProperties = {
  padding: 14, background: 'var(--color-surface)',
  border: '1px solid var(--color-border)', borderRadius: 8,
};
const cardTitle: React.CSSProperties = {
  fontWeight: 600, fontSize: 14, marginBottom: 10,
  display: 'flex', alignItems: 'center', gap: 6,
};
const muted: React.CSSProperties = { fontSize: 12, color: 'var(--color-text-muted)' };

function SlotStatusIcon({ status }: { status: string }) {
  const s = SLOT_STATUS_STYLE[status] || SLOT_STATUS_STYLE.pending;
  return <Icon name={s.icon} size={14} style={{ color: s.color }} />;
}

function SlotStatusLabel({ status }: { status: string }) {
  const s = SLOT_STATUS_STYLE[status] || SLOT_STATUS_STYLE.pending;
  return <span style={{ fontSize: 11, color: s.color, fontWeight: 500 }}>{s.label}</span>;
}

export function StagePageP5({ projectId, runId = '', stageStatus: _stageStatus, onReExecute: _onReExecute }: Props) {
  const [p5Input, setP5Input] = useState<any>(null);
  const [gate, setGate] = useState<GateInfo>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const rid = runId || '';
      const idxRes = rid
        ? fetch(`/api/projects/${projectId}/runs/${rid}/p5/input`)
        : Promise.resolve(null);
      const gateRes = fetch(`/api/projects/${projectId}/gates/active`);

      const [idx, g] = await Promise.all([idxRes, gateRes]);
      if (idx && idx.ok) setP5Input((await idx.json()).data || (await idx.json()));
      if (g.ok) {
        const gd = (await g.json()).data || (await g.json());
        setGate(gd && gd.gate_id ? gd : null);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [projectId, runId]);

  const isGateOpen = gate && gate.gate_status === 'waiting_decision';
  // R12-18 修复 R12-4-04：优先使用 p5_validation_report（handler 执行后持久化的真实结果）
  const p5Report = p5Input?.p5_validation_report;
  const validationPlan = p5Report?.validation_plan || p5Input?.validation_plan;
  const slots: any[] = validationPlan?.slots || [];
  const verifyResults: any[] = p5Report?.verify_results || p5Input?.verify_results || [];
  const conditionalResults: any[] = p5Report?.conditional_results || p5Input?.conditional_results || [];
  const evidenceGaps: any[] = p5Input?.evidence_gaps || [];
  const reworkItems: any[] = verifyResults.filter((vr: any) => !vr.passed);
  const canComplete = validationPlan?.can_be_completed || false;
  // ISSUE-04 修复：/p5/input 序列化字段为 evidence_refs/output_code_refs/patch_refs（无 p4_ 前缀，
  // 见 p5_input_service.p4_input_facts_to_dict），旧版读 p4_ 前缀致「P4 产物引用」整卡永不渲染。
  const p4EvidenceRefs: string[] = p5Input?.evidence_refs || [];
  const p4OutputRefs: string[] = p5Input?.output_code_refs || [];
  const p4PatchRefs: string[] = p5Input?.patch_refs || [];

  const hardRequiredValidated = slots.filter(
    (s: any) => s.slot_type === 'hard_required' && s.status === 'validated').length;
  const hardRequiredTotal = slots.filter(
    (s: any) => s.slot_type === 'hard_required').length;
  const conditionalPassed = slots.filter(
    (s: any) => s.slot_type === 'conditional' && s.status === 'validated').length;
  const conditionalTotal = slots.filter(
    (s: any) => s.slot_type === 'conditional').length;
  const evidenceGapCount = slots.filter(
    (s: any) => s.status === 'evidence_gap' || s.status === 'needs_user_input').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, ...card }}>
        <Icon name="evidence" size={18} style={{ color: 'var(--color-primary)' }} />
        <strong>P5&nbsp;验证</strong>
        <span style={{ marginLeft: 'auto' }}>
          {!loading && (
            <span style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 4,
              background: canComplete ? 'var(--green)' : 'var(--amber)',
              color: '#fff', fontWeight: 600,
            }}>
              {canComplete ? '验证通过' : '验证未完成'}
            </span>
          )}
        </span>
      </div>

      {error && <div style={{ ...card, color: 'var(--red)' }}>加载失败：{error}</div>}
      {loading && <div style={{ ...card, color: 'var(--color-text-muted)' }}>加载中…</div>}

      {!loading && !p5Input && !error && (
        <div style={{ ...card, color: 'var(--color-text-muted)' }}>
          P4 完成后，P5 将在此展示全量验证结果（构建 / 运行 / 测试 / 静态检查）。
        </div>
      )}

      {p5Input ? (
        <>
          {/* 1. 验证进度总览 */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="stage" size={14} style={{ color: 'var(--color-primary)' }} />
              验证进度
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              <div style={{ textAlign: 'center', padding: 10, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--green)' }}>
                  {hardRequiredValidated}/{hardRequiredTotal}
                </div>
                <div style={muted}>硬必需通过</div>
              </div>
              <div style={{ textAlign: 'center', padding: 10, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--amber)' }}>
                  {conditionalPassed}/{conditionalTotal}
                </div>
                <div style={muted}>条件通过</div>
              </div>
              <div style={{ textAlign: 'center', padding: 10, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: evidenceGapCount > 0 ? 'var(--red)' : 'var(--green)' }}>
                  {evidenceGapCount}
                </div>
                <div style={muted}>证据缺失</div>
              </div>
            </div>
          </div>

          {/* 2. 10 槽位验证详情 */}
          {slots.length > 0 ? (
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="success" size={14} style={{ color: 'var(--color-primary)' }} />
                验证槽位（10 项）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {slots.map((s: any) => (
                  <div key={s.slot_id} style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
                    background: 'var(--color-bg)', borderRadius: 6, border: '1px solid var(--color-border)',
                  }}>
                    <SlotStatusIcon status={s.status} />
                    <span style={{ fontSize: 12, fontWeight: 500, minWidth: 120 }}>{s.description || s.slot_id}</span>
                    <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3,
                      background: s.slot_type === 'hard_required' ? '#fef2f2' :
                                  s.slot_type === 'conditional' ? '#fffbeb' : 'var(--color-surface)',
                      color: s.slot_type === 'hard_required' ? 'var(--red)' :
                             s.slot_type === 'conditional' ? 'var(--amber)' : 'var(--color-text-muted)',
                    }}>
                      {SLOT_TYPE_LABEL[s.slot_type]}
                    </span>
                    <SlotStatusLabel status={s.status} />
                    {s.command ? (
                      <code style={{ fontSize: 10, background: 'var(--color-surface)', padding: '1px 4px',
                        borderRadius: 3, marginLeft: 'auto' }}>{s.command}</code>
                    ) : null}
                    {(s.exit_code !== undefined && s.exit_code !== null) ? (
                      <span style={{ fontSize: 10, color: s.exit_code === 0 ? 'var(--green)' : 'var(--red)' }}>
                        exit={s.exit_code}
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 3. 条件槽位真实命令结果 */}
          {conditionalResults.length > 0 ? (
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="run" size={14} style={{ color: 'var(--color-primary)' }} />
                真实命令执行结果（D-105①）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {conditionalResults.map((cr: any) => (
                  <div key={cr.slot_id} style={{
                    padding: 10, background: 'var(--color-bg)', borderRadius: 6,
                    borderLeft: `3px solid ${cr.passed ? 'var(--green)' : cr.gate_required ? '#8b5cf6' : 'var(--red)'}`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                      <strong style={{ fontSize: 12 }}>{cr.slot_id}</strong>
                      <code style={{ fontSize: 10, background: 'var(--color-surface)', padding: '1px 4px' }}>
                        {cr.command || '—'}
                      </code>
                      <span style={{ fontSize: 10, marginLeft: 'auto' }}>
                        exit=<strong style={{ color: cr.passed ? 'var(--green)' : 'var(--red)' }}>{cr.exit_code ?? '—'}</strong>
                        {(cr.elapsed_ms != null) ? (
                          <span style={{ marginLeft: 6, color: 'var(--color-text-muted)' }}>{cr.elapsed_ms}ms</span>
                        ) : null}
                      </span>
                    </div>
                    {cr.gate_required ? (
                      <div style={{ fontSize: 11, color: '#8b5cf6', padding: '4px 0' }}>
                        ⚠ 安全拦截：{cr.gate_reason}
                      </div>
                    ) : null}
                    {(!cr.passed && !cr.gate_required && cr.issues && cr.issues.length > 0) ? (
                      <div style={{ fontSize: 11, color: 'var(--red)' }}>
                        问题：{cr.issues.join(', ')}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 4. Evidence Gap 列表 */}
          {evidenceGaps.length > 0 ? (
            <div style={{ ...card, borderColor: 'var(--amber)' }}>
              <div style={cardTitle}>
                <Icon name="evidence" size={14} style={{ color: 'var(--amber)' }} />
                证据缺口（Evidence Gap）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {evidenceGaps.map((g: any) => (
                  <div key={g.gap_id || g} style={{
                    padding: 8, background: '#fffbeb', borderRadius: 4,
                    fontSize: 12,
                  }}>
                    <strong>{g.evidence_type || g.gap_id}</strong>
                    {g.description ? <span style={{ marginLeft: 6 }}>## {g.description}</span> : null}
                    {g.blocking ? <span style={{ marginLeft: 6, color: 'var(--red)', fontSize: 10 }}>阻塞</span> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 5. 返工/重试建议 */}
          {reworkItems.length > 0 ? (
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="refresh" size={14} style={{ color: 'var(--amber)' }} />
                返工建议
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {reworkItems.map((vr: any) => (
                  <li key={vr.slot_id} style={{ marginBottom: 4 }}>
                    <strong>{vr.slot_id}</strong> {vr.status}
                    {(vr.issues || []).map((i: any) => (
                      <span key={i.type}> — {i.detail}</span>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* 6. P5→P6 Gate */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="gate" size={14} style={{ color: 'var(--color-primary)' }} />
              P5→P6 晋级 Gate
            </div>
            {isGateOpen ? (
              <div style={{ padding: 10, background: '#fffbeb', borderRadius: 6, fontSize: 12 }}>
                <p>Gate <code>{gate.gate_id}</code> 等待决策。</p>
                {gate.summary ? <p style={muted}>{gate.summary}</p> : null}
                {(evidenceGapCount > 0) ? (
                  <p style={{ color: 'var(--amber)', fontWeight: 500 }}>
                    ⚠ 存在 {evidenceGapCount} 项证据缺口，需确认接受风险后方可晋级。
                  </p>
                ) : null}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                {(canComplete && evidenceGapCount === 0)
                  ? '✓ 所有验证通过，Gate 将在 P5 完成后创建。'
                  : canComplete
                    ? '△ 验证通过但存在证据缺口，需用户确认。'
                    : '验证未完成，Gate 尚未创建。'}
              </div>
            )}
          </div>

          {/* 7. P4 产物引用 */}
          {(p4OutputRefs.length > 0 || p4PatchRefs.length > 0 || p4EvidenceRefs.length > 0) ? (
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="artifact" size={14} style={{ color: 'var(--color-primary)' }} />
                P4 产物引用
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
                {(p4OutputRefs.length > 0) ? <div>产出代码：{p4OutputRefs.length} 个文件</div> : null}
                {(p4PatchRefs.length > 0) ? <div>变更补丁：{p4PatchRefs.length} 个文件</div> : null}
                {(p4EvidenceRefs.length > 0) ? <div>验证证据：{p4EvidenceRefs.length} 项</div> : null}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
