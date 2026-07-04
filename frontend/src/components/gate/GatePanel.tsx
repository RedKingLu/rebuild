/** GatePanel — R9-3G-B: Expandable gate review panel with material viewer.
 *  Click banner → Modal with left material list + right content preview.
 *  Supports approve / request_changes / reject decisions.
 */
import { useState, useEffect, useCallback } from 'react';
import { Modal } from '../ui/Modal';

interface MaterialItem {
  path: string;
  label: string;
  type: 'json' | 'markdown';
}

function labelForRef(ref: string): MaterialItem {
  const path = ref.startsWith('artifacts/') ? ref : `artifacts/${ref}`;
  const file = path.split('/').pop() || path;
  const type: 'json' | 'markdown' = file.endsWith('.md') ? 'markdown' : 'json';
  const stem = file.replace(/\.(json|md)$/i, '');
  // Known real StageReport / domain artifact stems → 中文标签
  const known: Record<string, string> = {
    intake_report: '接入报告 (intake_report)',
  };
  let label = known[stem];
  if (!label) {
    if (stem.endsWith('_start_plan')) label = `起始计划报告 (${stem})`;
    else if (stem.endsWith('_construction')) label = `施工报告 (${stem})`;
    else if (stem.endsWith('_acceptance')) label = `验收报告 (${stem})`;
    else if (stem.endsWith('_task_plans')) label = `Task Plan 批次 (${stem})`;
    else if (stem.endsWith('_task_graph')) label = `TaskGraph (${stem})`;
    else if (stem.endsWith('_stage_plan')) label = `Stage Plan (${stem})`;
    else if (stem.endsWith('_assessment_report')) label = `评估报告 (${stem})`;
    else if (stem.endsWith('_risk_list')) label = `风险清单 (${stem})`;
    else if (stem.endsWith('_blocker_list')) label = `阻塞项清单 (${stem})`;
    else if (stem.endsWith('_validation_gaps')) label = `验证缺口 (${stem})`;
    else if (stem.endsWith('_resource_needs')) label = `资源需求 (${stem})`;
    else label = stem;
  }
  return { path, label, type };
}

// B-P0-FAKE-1 (R11-3): the review materials come from the Gate's REAL artifact_refs
// (produced by the LangGraph stage node / StageReports), not a hardcoded per-stage
// filename list. The old P0 list pointed at fabricated files (p0_execution_record /
// p0_construction_report / p0_review_pass); those are no longer produced (D-101).
// The per-stage fallbacks below use the REAL StageReport names and are only used when
// a Gate carries no artifact_refs (degraded / legacy gate).
function getMaterials(gate: any): MaterialItem[] {
  const refs: string[] = Array.isArray(gate?.artifact_refs) ? gate.artifact_refs : [];
  if (refs.length > 0) return refs.map(labelForRef);
  const stage = gate?.stage || 'p0';
  if (stage === 'p0') {
    return ['intake_report.json', 'p0_start_plan.json', 'p0_construction.json', 'p0_acceptance.json']
      .map(f => labelForRef(f));
  }
  if (stage === 'p2') {
    return ['p2_assessment_report.json', 'p2_risk_list.json', 'p2_blocker_list.json',
      'p2_validation_gaps.json', 'p2_resource_needs.json'].map(f => labelForRef(f));
  }
  if (stage === 'p3') {
    return ['p3_stage_plan.json', 'p3_task_plans.json', 'p3_task_graph.json'].map(f => labelForRef(f));
  }
  // p1 (default): real StageReport names
  return ['p1_start_plan.json', 'p1_construction.json', 'p1_acceptance.json'].map(f => labelForRef(f));
}

interface Props {
  gate: any;
  projectId: string;
  onDecided: () => void;
  onProfilingStart?: () => void;
}

export function GatePanel({ gate, projectId, onDecided }: Props) {
  const materialLabels = getMaterials(gate);
  const [expanded, setExpanded] = useState(false);
  const [activeMaterial, setActiveMaterial] = useState<string>('');
  const [materialContents, setMaterialContents] = useState<Record<string, string>>({});
  const [loadingMaterial, setLoadingMaterial] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [decisionFeedback, setDecisionFeedback] = useState<string | null>(null);
  const [reworkHint, setReworkHint] = useState(false);
  // UX-5: collect the user's reason for the decision (required on reject/request_changes).
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState<string | null>(null);

  const gateId = gate?.gate_id;
  // B-PLAN-1: plan_review gates review the pre-execution stage plan (D-025/D-026).
  const gateLabel = gate?.gate_type === 'stage_promotion' ? '阶段晋级 Gate'
    : gate?.gate_type === 'plan_review' ? '计划审核 Gate'
    : gate?.gate_type === 'source_pending' ? '源码补全 Gate'
    : (gate?.gate_type || 'Gate');

  // Load material content when a material is selected
  const loadMaterial = useCallback(async (materialPath: string) => {
    if (materialContents[materialPath]) return;
    setLoadingMaterial(materialPath);
    try {
      const resp = await fetch(`/api/projects/${projectId}/file?path=${encodeURIComponent(materialPath)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const content = data?.data?.content || '';
      setMaterialContents(prev => ({ ...prev, [materialPath]: content }));
    } catch {
      setMaterialContents(prev => ({ ...prev, [materialPath]: '// 文件不可用或尚未生成' }));
    } finally {
      setLoadingMaterial(null);
    }
  }, [projectId, materialContents]);

  // Auto-load first material on expand
  useEffect(() => {
    if (expanded && materialLabels.length > 0) {
      loadMaterial(materialLabels[0].path);
    }
  }, [expanded]);

  const handleDecision = async (decision: string) => {
    if (!gateId) return;
    // UX-5: reject / request_changes require a reason (no dead-end rejections).
    const needsReason = decision === 'reject' || decision === 'request_changes';
    if (needsReason && !reason.trim()) {
      setReasonError('请填写拒绝/请求修改的原因（agent 将据此返工）');
      return;
    }
    setDeciding(true);
    setDecisionFeedback(null);
    setReasonError(null);
    try {
      const runId = gate.run_id || 'unknown';
      const stage = gate.stage || 'p0';
      const resp = await fetch(`/api/projects/${projectId}/runs/${runId}/stages/${stage}/promotion-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // UX-5: pass the real user reason (used downstream for rework-notes artifact).
        body: JSON.stringify({ decision, reason: reason.trim() || `User ${decision} via GatePanel` }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      if (decision === 'request_changes') {
        setReworkHint(true);
        setDecisionFeedback('阶段需要返工。请回到阶段页面重新执行该阶段任务。');
        setDeciding(false);
        return;
      }
      if (decision === 'reject') {
        setDecisionFeedback('阶段已被拒绝。');
        setDeciding(false);
        return;
      }

      // B-PLAN-1 (R11-3): approve just resumes the LangGraph thread (promotion-decision
      // above drives FlowRuntime.resume). The graph advances the stage itself —
      // approving a P0 plan_review gate resumes into real P0 execution; approving the
      // P0 promotion gate resumes into P1. We no longer POST /profile here (that was a
      // separate, fabricated P1 path — p1_stage_plan/execution_record/construction_report/
      // review_pass were template/hardcoded/always-pass; forbidden by D-101). P1 review
      // materials now come from the real graph P1 node (RealP1Handler → StageReports).
      onDecided();
      setExpanded(false);
    } catch (e: any) {
      setDecisionFeedback(`决策失败: ${e.message}`);
    } finally {
      setDeciding(false);
    }
  };

  const activeMat = materialLabels.find(m => m.path === activeMaterial);
  const content = activeMaterial ? materialContents[activeMaterial] : null;

  // Simple markdown render for .md files
  const renderMarkdown = (md: string) => {
    const lines = md.split('\n');
    return lines.map((line, i) => {
      if (line.startsWith('# ')) return <h2 key={i} style={{ fontSize: 16, margin: '0 0 8px' }}>{line.slice(2)}</h2>;
      if (line.startsWith('## ')) return <h3 key={i} style={{ fontSize: 14, margin: '8px 0 4px' }}>{line.slice(3)}</h3>;
      if (line.startsWith('- ')) return <li key={i} style={{ fontSize: 12, marginLeft: 16 }}>{line.slice(2)}</li>;
      if (line.startsWith('**') && line.endsWith('**')) return <b key={i} style={{ fontSize: 12 }}>{line.slice(2, -2)}</b>;
      if (line.startsWith('```')) return <code key={i} style={{ display: 'block', padding: '4px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11, margin: '4px 0' }}>{line.slice(3, -3)}</code>;
      return <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>{line || ' '}</div>;
    });
  };

  // Collapsed banner (always visible when gate is active)
  return (
    <>
      <div style={{
        padding: '10px 16px', background: 'var(--amber-soft, #fff8e1)',
        borderBottom: '2px solid var(--amber)', flexShrink: 0,
        display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer',
      }} onClick={() => { setExpanded(true); setReason(''); setReasonError(null); }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>⚠ {gateLabel}</div>
          <div style={{ fontSize: 12, marginTop: 2 }}>{gate?.summary || gate?.reason}</div>
          {gate?.risk_level && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>风险级别: {gate.risk_level}</div>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>点击查看审核材料 →</div>
      </div>

      {/* Expanded Modal */}
      <Modal open={expanded} onClose={() => { setExpanded(false); setReworkHint(false); setDecisionFeedback(null); }}
        title={`Gate 审核 — ${gate?.stage?.toUpperCase?.() || 'P0'} · ${gateLabel}`} width={800}>
        {gate?.gate_type === 'plan_review' && (
          <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
            该阶段动作尚未执行。请审阅下方阶段计划后决定是否批准执行（批准后才会执行阶段动作）。
          </div>
        )}        <div style={{ display: 'flex', gap: 16, minHeight: 300 }}>
          {/* Left: Material list (40%) */}
          <div style={{ width: '40%', minWidth: 200, borderRight: '1px solid var(--color-border)', paddingRight: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>审核材料</div>
            {materialLabels.map(m => (
              <div key={m.path}
                onClick={() => { setActiveMaterial(m.path); loadMaterial(m.path); }}
                style={{
                  padding: '8px 10px', marginBottom: 4, borderRadius: 6, cursor: 'pointer',
                  fontSize: 12,
                  background: activeMaterial === m.path ? 'var(--color-primary-soft)' : 'var(--color-surface-subtle)',
                  color: activeMaterial === m.path ? 'var(--color-primary)' : 'var(--color-text)',
                  fontWeight: activeMaterial === m.path ? 600 : 400,
                }}>
                {m.label}
              </div>
            ))}
          </div>

          {/* Right: Material content (60%) */}
          <div style={{ flex: 1, overflow: 'auto', maxHeight: '50vh' }}>
            {!content && loadingMaterial && (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>加载中…</div>
            )}
            {content === '// 文件不可用或尚未生成' && (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>该材料尚未生成或不可用。</div>
            )}
            {content && activeMat?.type === 'json' && (
              <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                background: 'var(--color-surface-subtle)', padding: 12, borderRadius: 6, margin: 0 }}>
                {(() => { try { return JSON.stringify(JSON.parse(content), null, 2); } catch { return content; } })()}
              </pre>
            )}
            {content && activeMat?.type === 'markdown' && (
              <div style={{ padding: '4px 0' }}>{renderMarkdown(content)}</div>
            )}
            {!content && !loadingMaterial && (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>点击左侧材料查看内容</div>
            )}
          </div>
        </div>

        {/* Feedback status */}
        {decisionFeedback && (
          <div style={{
            marginTop: 12, padding: '8px 12px', borderRadius: 6, fontSize: 13,
            background: reworkHint ? 'var(--amber-soft, #fff8e1)' : 'var(--red-soft, #ffebee)',
            color: reworkHint ? 'var(--amber-text, #8d6e00)' : 'var(--red)',
          }}>
            {decisionFeedback}
          </div>
        )}

        {/* UX-5: decision reason — required when rejecting / requesting changes */}
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>
            决策原因 <span style={{ color: 'var(--red)' }}>*</span>
            <span style={{ fontSize: 10, marginLeft: 6 }}>（拒绝/请求修改必填；该原因将作为返工要求交付给 agent）</span>
          </div>
          <textarea
            value={reason}
            onChange={e => { setReason(e.target.value); if (reasonError) setReasonError(null); }}
            placeholder="例如：接入报告缺少数据库连接配置；请补充后重新提交…"
            rows={3}
            style={{
              width: '100%', padding: '8px 10px', border: `1px solid ${reasonError ? 'var(--red)' : 'var(--color-border)'}`,
              borderRadius: 6, fontSize: 12, resize: 'vertical', background: 'var(--color-surface)',
              color: 'var(--color-text)', fontFamily: 'inherit', boxSizing: 'border-box',
            }}
          />
          {reasonError && <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>{reasonError}</div>}
        </div>

        {/* Bottom action bar */}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
          <button className="btn sm" style={{ background: 'var(--green)', color: '#fff', fontSize: 12 }} disabled={deciding}
            onClick={() => handleDecision('approve')}>批准</button>
          <button className="btn sm ghost" style={{ fontSize: 12 }} disabled={deciding}
            onClick={() => handleDecision('request_changes')}>请求修改</button>
          <button className="btn sm ghost" style={{ color: 'var(--red)', fontSize: 12 }} disabled={deciding}
            onClick={() => handleDecision('reject')}>拒绝</button>
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" style={{ fontSize: 12 }} onClick={() => { setExpanded(false); setReworkHint(false); setDecisionFeedback(null); setReason(''); setReasonError(null); }}>
            关闭
          </button>
        </div>
      </Modal>
    </>
  );
}
