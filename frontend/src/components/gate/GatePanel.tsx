/** GatePanel — R9-3G-B: Expandable gate review panel with material viewer.
 *  Click banner → Modal with left material list + right content preview.
 *  Supports approve / request_changes / reject decisions.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Modal } from '../ui/Modal';
import { Icon } from '../ui/Icon';

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
    // R17.3-6 WP-3：WorkAgent / ValidationAgent（WP-2）产出的审核材料
    else if (stem.endsWith('_work_plan')) label = `动态工作计划 (${stem})`;
    else if (stem.endsWith('_gate_brief')) label = `Gate Brief 审核摘要 (${stem})`;
    else if (stem.endsWith('_claim_evidence_map')) label = `claim/fact-evidence 映射 (${stem})`;
    else if (stem.endsWith('_validation')) label = `独立验收结论 (${stem})`;
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
  // R17.3-6 WP-4：desensitization_release Gate 的脱敏风险说明。来源 = P6 交付包端点在含
  // 疑似密钥时返回的 422 detail.risk_explanation（真实后端数据，仅 path/pattern/count，
  // 无密钥明文 D-032）。前端仅展示，不拼凑（D-101）。
  const [desensRisk, setDesensRisk] = useState<any | null>(null);
  const [desensLoading, setDesensLoading] = useState(false);

  const gateId = gate?.gate_id;
  // R17-X (B-R17X-PLANREVIEW-1): the per-stage plan_review「欢迎门」was removed. The
  // one-time 欢迎/启动 step now lives in WorkspacePage; stage flow is controlled by the
  // stage_promotion Gate. plan_presentation (接入计划审核) is retained.
  const gateLabel = gate?.gate_type === 'stage_promotion' ? '阶段晋级 Gate'
    : gate?.gate_type === 'plan_presentation' ? '接入计划审核 Gate'
    : gate?.gate_type === 'source_pending' ? '源码补全 Gate'
    // R17.3-6 WP-4（EG-WP4-1）：新增两类安全 Gate 的中文标签。
    : gate?.gate_type === 'desensitization_release' ? '脱敏放行 Gate（高风险）'
    : gate?.gate_type === 'action_approval' ? '高风险动作审批 Gate'
    : (gate?.gate_type || 'Gate');

  // R17.3-6 WP-4（EG-WP4-1）：安全 Gate 走通用 Gate 决策端点、渲染专用风险面板，
  // 不复用阶段材料列表 / 阶段晋级端点（它们不是阶段晋级 Gate）。
  const isDesensGate = gate?.gate_type === 'desensitization_release';
  const isActionApproval = gate?.gate_type === 'action_approval';
  const isSecurityGate = isDesensGate || isActionApproval;

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

  // R17.3-6 WP-4：展开脱敏放行 Gate 时，拉取 P6 交付包端点的 422 风险说明（真实脱敏明细）。
  useEffect(() => {
    if (!expanded || !isDesensGate || desensRisk || desensLoading) return;
    const runId = gate?.run_id;
    if (!runId) return;
    setDesensLoading(true);
    (async () => {
      try {
        const resp = await fetch(`/api/projects/${projectId}/runs/${runId}/p6/package`);
        // FastAPI 默认 HTTPException 序列化为 { detail: ... }；含疑似密钥时 detail 携带 risk_explanation。
        const body = await resp.json().catch(() => ({}));
        const detail = body?.detail;
        if (detail && typeof detail === 'object' && detail.risk_explanation) {
          setDesensRisk(detail.risk_explanation);
        }
      } catch {
        /* 忽略：下方以 gate.summary 命中概览作为兜底展示，不伪造明细 */
      } finally {
        setDesensLoading(false);
      }
    })();
  }, [expanded, isDesensGate, projectId, gate?.run_id]);

  const handleDecision = async (decision: string) => {
    if (!gateId) return;
    // UX-5: reject / request_changes require a reason (no dead-end rejections).
    const needsReason = decision === 'reject' || decision === 'request_changes';
    if (needsReason && !reason.trim()) {
      setReasonError('请填写拒绝/请求修改的原因（agent 将据此返工）');
      return;
    }
    // R17.3-6 WP-4（EG-WP4-1）：安全 Gate（脱敏放行 / 高风险动作审批）不是阶段晋级 Gate，
    // 决策走通用 Gate 决策端点 /gates/{gate_id}/decision（GateService.decide → 写 Audit）。
    // 脱敏放行批准后，P6 交付端点重取时 find_approved_desensitization_override 命中即放行；
    // action_approval 批准后，ACP 端 HITL 轮询取到 approved 即放行本次执行。
    if (isSecurityGate) {
      setDeciding(true);
      setDecisionFeedback(null);
      setReasonError(null);
      try {
        const resp = await fetch(`/api/projects/${projectId}/gates/${gateId}/decision`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ decision, reason: reason.trim() || `User ${decision} via GatePanel` }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        onDecided();
        setExpanded(false);
      } catch (e: any) {
        setDecisionFeedback(`决策失败: ${e.message}`);
      } finally {
        setDeciding(false);
      }
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
      // approving a promotion gate resumes the stage into the next one. We no longer
      // POST /profile here (that was a separate, fabricated P1 path — p1_stage_plan/
      // execution_record/construction_report/review_pass were template/hardcoded/
      // always-pass; forbidden by D-101). P1 review materials now come from the real
      // graph P1 node (RealP1Handler → StageReports).
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

  // Chinese field label mapping
  const FIELD_LABELS: Record<string, string> = {
    stage: '阶段', kind: '报告类型', generated_at: '生成时间', goal: '目标',
    acceptance_criteria: '验收标准', planned_actions: '计划步骤', status: '状态',
    verdict: '裁决', summary: '摘要', evidence_refs: '证据引用', error: '错误信息',
    project_id: '项目 ID', artifact_type: '产物类型', source_type: '源码类型',
    file_count: '文件数量', materialization_status: '物化状态', artifact_id: '产物 ID',
    objective: '目标', scope: '范围', risk_level: '风险等级', gate_policy: 'Gate 策略',
    completion_criteria: '完成标准', node_count: '节点数', edge_count: '边数',
    degraded: '降级模式', graph_status: '执行状态', batch_id: '批次 ID',
    task_plan_refs: '任务计划引用', batch_risk_level: '批次风险等级',
    gate_required: '需要 Gate', stage_plan_ref: 'Stage Plan 引用',
    task_graph_ref: 'TaskGraph 引用', analysis_only: '仅供分析', model_used: '使用模型',
    report: '评估报告', items: '条目', actions_taken: '已执行操作',
    criteria_results: '标准结果', plan_summary: '计划摘要', key_decisions: '关键决策',
    estimated_tasks: '预估任务数', milestones: '里程碑',
    // R17.3-6 WP-3：construction / acceptance 真实字段（FE-01）
    rounds: '施工轮次', actions: '执行动作', produced_artifacts: '产出产物',
    passed: '是否通过', issues: '问题', recommendations: '改进建议', reviewer: '审核者',
    round: '轮次',
    // R17.3-6 WP-3：WorkAgent 动态工作计划 / Gate Brief / 独立验收 / claim-evidence（WP-2）
    generated_by: '生成方', based_on: '依据事实', planned_actions_detail: '计划动作',
    skill_ref: '使用 Skill', risks_foreseen: '预见风险', skill_id: 'Skill ID', name: '名称',
    what_happened: '本阶段所做', key_artifacts: '关键产物', risks: '风险',
    validation_verdict: '独立验收裁决', claim_evidence_summary: 'claim/证据摘要',
    honest_notes: '诚实说明', decision_options: '可选决策', tool: '工具', rationale: '理由',
    checks: '验收检查项', claim_evidence_verification: 'claim/证据核验',
    read_from_disk_only: '仅读落盘产物', agent_id: 'Agent ID', validated_at: '验收时间',
    map_type: '映射类型', entries: '条目', statement: '陈述', produced_by: '产出方',
    bindings: '绑定引用', inline_citation: '内联引用', verified_on_disk: '落盘校验',
    cited_upstream_refs: '引用上游', invalid_cited_refs: '无效引用', upstream_refs: '上游产物',
    artifact_refs: '产物引用', trace_refs: 'Trace 引用',
    audit_refs: 'Audit 引用', sha256: 'SHA-256', ref: '引用', level: '级别',
    desc: '描述', source_ref: '来源引用', item: '检查项', reason: '理由',
    total: '总数', resolved: '已解析', unresolved: '未解析', issues_count: '问题数',
    with_inline_citation: '含内联引用',
  };

  // Collapsible long-string component (inline function component)
  function LongValue({ text }: { text: string }) {
    const [open, setOpen] = useState(false);
    if (text.length <= 200) return <span style={{ fontSize: 12 }}>{text}</span>;
    return (
      <span style={{ fontSize: 12 }}>
        {open ? text : `${text.slice(0, 200)}…`}
        <button onClick={() => setOpen(!open)}
          style={{ marginLeft: 4, fontSize: 11, color: 'var(--color-primary)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
          {open ? '收起' : '展开'}
        </button>
      </span>
    );
  }

  // Generic field card renderer
  function renderFieldCard(key: string, value: unknown, depth = 0): React.ReactNode {
    const label = FIELD_LABELS[key] || key;
    const keyStyle: React.CSSProperties = { fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, minWidth: 80, marginRight: 8, flexShrink: 0 };
    const rowStyle: React.CSSProperties = { display: 'flex', alignItems: 'flex-start', marginBottom: 6, paddingLeft: depth * 12 };
    if (value === null || value === undefined) return null;
    if (typeof value === 'boolean') return <div key={key} style={rowStyle}><span style={keyStyle}>{label}</span><span style={{ fontSize: 12 }}>{value ? '✓' : '✗'}</span></div>;
    if (typeof value === 'number') return <div key={key} style={rowStyle}><span style={keyStyle}>{label}</span><span style={{ fontSize: 12 }}>{String(value)}</span></div>;
    if (typeof value === 'string') return <div key={key} style={rowStyle}><span style={keyStyle}>{label}</span><LongValue text={value} /></div>;
    if (Array.isArray(value)) {
      if (value.length === 0) return null;
      const isStringArr = value.every((v: unknown) => typeof v === 'string');
      return (
        <div key={key} style={{ marginBottom: 8, paddingLeft: depth * 12 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}：</div>
          {isStringArr
            ? (value as string[]).map((v, i) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>• {v}</div>)
            : (value as object[]).map((v, i) => (
              <div key={i} style={{ marginLeft: 12, marginBottom: 6, padding: '6px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
                {typeof v === 'object' && v !== null
                  ? Object.entries(v as Record<string, unknown>).map(([k2, v2]) => renderFieldCard(k2, v2, 0))
                  : <span style={{ fontSize: 12 }}>{String(v)}</span>}
              </div>
            ))
          }
        </div>
      );
    }
    if (typeof value === 'object') {
      const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== null && v !== undefined);
      if (entries.length === 0) return null;
      return (
        <div key={key} style={{ marginBottom: 8, paddingLeft: depth * 12 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}：</div>
          <div style={{ marginLeft: 12, padding: '6px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
            {entries.map(([k2, v2]) => renderFieldCard(k2, v2, 0))}
          </div>
        </div>
      );
    }
    return null;
  }

  // Smart JSON renderer: branches on kind/artifact_type, falls back to field cards
  function renderJsonContent(rawContent: string): React.ReactNode {
    let j: Record<string, unknown>;
    try { j = JSON.parse(rawContent); } catch {
      return <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: 'var(--color-surface-subtle)', padding: 12, borderRadius: 6, margin: 0 }}>{rawContent}</pre>;
    }
    const kind = j.kind as string | undefined;
    // start_plan — kind 明确为 start_plan，或无 kind 的遗留计划产物（goal+planned_actions）。
    // 注意：work_plan（WP-2）同样含 goal/planned_actions 但其 planned_actions 为对象数组，
    // 必须由下方 kind==='work_plan' 分支处理，故此处用 !kind 排除。
    if (kind === 'start_plan' || (!kind && j.goal && j.planned_actions)) {
      return (
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>{j.goal as string || '接入计划'}</div>
          {Array.isArray(j.acceptance_criteria) && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>验收标准：</div>
              {(j.acceptance_criteria as string[]).map((c, i) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>✓ {c}</div>)}
            </div>
          )}
          {Array.isArray(j.planned_actions) && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>计划步骤：</div>
              {(j.planned_actions as string[]).map((a, i) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>{i + 1}. {a}</div>)}
            </div>
          )}
        </div>
      );
    }
    // construction report — FE-01：对齐 stage_reports.py construction() 真实字段
    // （rounds / actions / produced_artifacts），旧版误读 status/summary/actions_taken 使卡片空白。
    if (kind === 'construction') {
      const rounds = Array.isArray(j.rounds) ? j.rounds : [];
      const actions = Array.isArray(j.actions) ? j.actions : [];
      const producedArtifacts = Array.isArray(j.produced_artifacts) ? j.produced_artifacts : [];
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>施工报告 — {(j.stage as string || '').toUpperCase()}</div>
          {rounds.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>施工轮次：</div>
              {rounds.map((r: any, i: number) => (
                <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 4, padding: '6px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
                  <div>第 {r.round ?? i + 1} 轮 · 结果：
                    <span style={{ color: r.status === 'passed' ? 'var(--green)' : 'var(--amber)', fontWeight: 600 }}> {r.status}</span>
                  </div>
                  {Array.isArray(r.issues) && r.issues.length > 0 && (
                    <div style={{ color: 'var(--color-text-muted)', marginTop: 2 }}>问题：{r.issues.map((x: any) => typeof x === 'string' ? x : (x.detail || x.type || JSON.stringify(x))).join('；')}</div>
                  )}
                </div>
              ))}
            </div>
          )}
          {actions.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>执行动作：</div>
              {actions.map((a, i) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>• {String(a)}</div>)}
            </div>
          )}
          {producedArtifacts.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>产出产物（{producedArtifacts.length}）：</div>
              {producedArtifacts.map((a, i) => <div key={i} style={{ fontSize: 11, fontFamily: 'var(--mono)', marginLeft: 12, marginBottom: 2 }}>{String(a)}</div>)}
            </div>
          )}
          {renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // acceptance report — FE-01：对齐 stage_reports.py acceptance() 真实字段
    // （passed / issues / recommendations / reviewer），旧版误读 verdict/criteria_results 使卡片空白。
    if (kind === 'acceptance') {
      const issues = Array.isArray(j.issues) ? j.issues : [];
      const recs = Array.isArray(j.recommendations) ? j.recommendations : [];
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>验收报告 — {(j.stage as string || '').toUpperCase()}</div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>验收结论：
            <span style={{ color: j.passed ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{j.passed ? ' 通过' : ' 未通过'}</span>
          </div>
          {issues.length > 0 ? (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>问题（{issues.length}）：</div>
              {issues.map((x: any, i: number) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2, color: 'var(--red)' }}>• {typeof x === 'string' ? x : (x.detail || JSON.stringify(x))}</div>)}
            </div>
          ) : <div style={{ fontSize: 12, marginBottom: 6, color: 'var(--color-text-muted)' }}>无待处理问题。</div>}
          {recs.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>改进建议：</div>
              {recs.map((x: any, i: number) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>• {typeof x === 'string' ? x : JSON.stringify(x)}</div>)}
            </div>
          )}
          {renderFieldCard('reviewer', j.reviewer)}{renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // R17.3-6 WP-2/WP-3：动态工作计划报告（WorkAgent 据真实项目事实合成，AGT-03）
    if (kind === 'work_plan') {
      const plannedActions = Array.isArray(j.planned_actions) ? j.planned_actions : [];
      const criteria = Array.isArray(j.acceptance_criteria) ? j.acceptance_criteria : [];
      const skillRef = j.skill_ref as Record<string, unknown> | null;
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>动态工作计划 — {(j.stage as string || '').toUpperCase()}</div>
          {!!j.goal && <div style={{ fontSize: 12, marginBottom: 8 }}>{j.goal as string}</div>}
          {renderFieldCard('generated_by', j.generated_by)}
          {!!j.based_on && typeof j.based_on === 'object' && renderFieldCard('based_on', j.based_on)}
          {plannedActions.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>计划动作：</div>
              {plannedActions.map((a: any, i: number) => (
                <div key={i} style={{ marginLeft: 12, marginBottom: 6, padding: '6px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{i + 1}. {a.action || a}</div>
                  {a.tool && <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>工具：<code>{a.tool}</code></div>}
                  {a.rationale && <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>理由：{a.rationale}</div>}
                </div>
              ))}
            </div>
          )}
          {criteria.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>验收标准：</div>
              {criteria.map((c: string, i: number) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>• {c}</div>)}
            </div>
          )}
          {skillRef && <div style={{ fontSize: 12, marginBottom: 4 }}>使用 Skill：<code>{String(skillRef.name || skillRef.skill_id || '')}</code></div>}
          {renderFieldCard('risks_foreseen', j.risks_foreseen)}
          {renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // R17.3-6 WP-2/WP-3：Gate Brief 用户可读阶段审核摘要（AGT-02，D-101 真实内容）
    if (kind === 'gate_brief') {
      const keyArtifacts = Array.isArray(j.key_artifacts) ? j.key_artifacts : [];
      const risks = Array.isArray(j.risks) ? j.risks : [];
      const vv = (j.validation_verdict || {}) as Record<string, unknown>;
      const ces = (j.claim_evidence_summary || {}) as Record<string, any>;
      const passed = vv.passed as boolean | undefined;
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Gate Brief 审核摘要 — {(j.stage as string || '').toUpperCase()}</div>
          {!!j.what_happened && <div style={{ fontSize: 12, marginBottom: 10, lineHeight: 1.6 }}>{j.what_happened as string}</div>}
          {Object.keys(vv).length > 0 && (
            <div style={{ fontSize: 12, marginBottom: 8, padding: '6px 8px', background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
              独立验收裁决：<span style={{ color: passed ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{String(vv.verdict ?? (passed ? 'accepted' : 'rework'))}</span>
              <span style={{ marginLeft: 8, color: 'var(--color-text-muted)' }}>问题数：{String(vv.issues_count ?? 0)}</span>
              {vv.agent_id ? <span style={{ marginLeft: 8, color: 'var(--color-text-muted)', fontSize: 11 }}>Agent {String(vv.agent_id).slice(0, 8)}</span> : null}
            </div>
          )}
          {keyArtifacts.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>关键产物：</div>
              {keyArtifacts.map((a: any, i: number) => (
                <div key={i} style={{ fontSize: 11, fontFamily: 'var(--mono)', marginLeft: 12, marginBottom: 2, display: 'flex', gap: 8 }}>
                  <span style={{ flex: 1 }}>{a.ref || a}</span>
                  {a.sha256 && <span style={{ color: 'var(--color-text-muted)' }}>{String(a.sha256).slice(0, 8)}…</span>}
                </div>
              ))}
            </div>
          )}
          {risks.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>风险（{risks.length}）：</div>
              {risks.map((r: any, i: number) => (
                <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>
                  <span style={{ fontSize: 10, padding: '0 5px', borderRadius: 3, background: 'var(--color-surface-subtle)', marginRight: 6 }}>{r.level}</span>
                  {r.desc}
                </div>
              ))}
            </div>
          )}
          {Object.keys(ces).length > 0 && (
            <div style={{ fontSize: 12, marginBottom: 6 }}>
              claim/证据摘要：共 {ces.total ?? 0} 条，已解析 {ces.resolved ?? 0} 条
              {ces.inline_citation ? <span style={{ marginLeft: 6, color: 'var(--color-text-muted)' }}>（内联引用 {ces.inline_citation.with_inline_citation ?? 0}/{ces.inline_citation.total ?? 0}，无效引用 {(ces.inline_citation.invalid_cited_refs || []).length}）</span> : null}
            </div>
          )}
          {!!j.honest_notes && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 6 }}>诚实说明：{j.honest_notes as string}</div>}
          {renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // R17.3-6 WP-2/WP-3：独立 ValidationAgent 验收结论（D-082，独立身份+只读落盘）
    if (kind === 'validation') {
      const checks = Array.isArray(j.checks) ? j.checks : [];
      const issues = Array.isArray(j.issues) ? j.issues : [];
      const recs = Array.isArray(j.recommendations) ? j.recommendations : [];
      const cev = (j.claim_evidence_verification || {}) as Record<string, any>;
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>独立验收结论 — {(j.stage as string || '').toUpperCase()}</div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>裁决：
            <span style={{ color: j.passed ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}> {String(j.verdict ?? (j.passed ? 'accepted' : 'rework'))}</span>
            {j.reviewer ? <span style={{ marginLeft: 8, color: 'var(--color-text-muted)', fontSize: 11 }}>审核者：{j.reviewer as string}</span> : null}
            {j.agent_id ? <span style={{ marginLeft: 8, color: 'var(--color-text-muted)', fontSize: 11 }}>Agent {String(j.agent_id).slice(0, 8)}</span> : null}
          </div>
          {j.read_from_disk_only ? <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 6 }}>（独立验收：仅读取落盘产物 / Evidence，未继承施工进程内推理）</div> : null}
          {checks.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>验收检查项：</div>
              {checks.map((c: any, i: number) => (
                <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 3 }}>
                  <span style={{ color: c.passed ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{c.passed ? '通过' : '未通过'}</span>
                  <span style={{ marginLeft: 6 }}>{c.item}</span>
                  {c.reason && <span style={{ color: 'var(--color-text-muted)' }}> — {c.reason}</span>}
                  {c.evidence_ref && <div style={{ fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--color-text-muted)', marginLeft: 12 }}>证据：{c.evidence_ref}</div>}
                </div>
              ))}
            </div>
          )}
          {issues.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--red)' }}>问题（{issues.length}）：</div>
              {issues.map((x: any, i: number) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2, color: 'var(--red)' }}>• {typeof x === 'string' ? x : (x.detail || JSON.stringify(x))}</div>)}
            </div>
          )}
          {recs.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: 'var(--color-text-muted)' }}>改进建议：</div>
              {recs.map((x: any, i: number) => <div key={i} style={{ fontSize: 12, marginLeft: 12, marginBottom: 2 }}>• {typeof x === 'string' ? x : JSON.stringify(x)}</div>)}
            </div>
          )}
          {Object.keys(cev).length > 0 && (
            <div style={{ fontSize: 12, marginBottom: 6, color: 'var(--color-text-muted)' }}>
              claim/证据核验：共 {cev.total ?? 0} 条，已解析 {cev.resolved ?? 0} 条{(cev.unresolved || []).length > 0 ? `，未解析 ${(cev.unresolved || []).length} 条` : ''}
            </div>
          )}
          {renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // R17.3-6 WP-2/WP-3：claim/fact-evidence 映射（AGT-05/EVI-01，每条 claim 显示绑定引用）
    if (kind === 'claim_evidence_map') {
      const entries = Array.isArray(j.entries) ? j.entries : [];
      const mapType = j.map_type as string;
      const refLine = (label: string, refs: any) => {
        const arr = Array.isArray(refs) ? refs : [];
        if (arr.length === 0) return null;
        return <div style={{ fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--color-text-muted)', marginLeft: 12 }}>{label}：{arr.join('，')}</div>;
      };
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
            {mapType === 'claim_evidence' ? 'claim-evidence 映射' : 'fact-evidence 映射'} — {(j.stage as string || '').toUpperCase()}
          </div>
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 8 }}>
            共 {entries.length} 条{mapType === 'claim_evidence' ? '（LLM claim，内联引用上游证据）' : '（确定性事实，绑定证据）'}
          </div>
          {entries.map((e: any, i: number) => {
            const b = e.bindings || {};
            const invalid = Array.isArray(b.invalid_cited_refs) ? b.invalid_cited_refs : [];
            return (
              <div key={e.id || i} style={{ marginBottom: 8, padding: '8px 10px', background: 'var(--color-surface-subtle)', borderRadius: 4, borderLeft: `3px solid ${e.verified_on_disk ? 'var(--green)' : 'var(--amber)'}` }}>
                <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>{e.statement || e.id}</div>
                <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                  产出方：{e.produced_by === 'llm' ? 'LLM' : e.produced_by === 'deterministic_tool' ? '确定性工具' : e.produced_by || '—'}
                  <span style={{ marginLeft: 8 }}>内联引用：{e.inline_citation ? '是' : '否'}</span>
                  <span style={{ marginLeft: 8, color: e.verified_on_disk ? 'var(--green)' : 'var(--amber)' }}>落盘校验：{e.verified_on_disk ? '通过' : '未通过'}</span>
                </div>
                {refLine('产物引用', b.artifact_refs)}
                {refLine('证据引用', b.evidence_refs)}
                {refLine('引用上游', b.cited_upstream_refs)}
                {refLine('Trace', b.trace_refs)}
                {b.sha256 && <div style={{ fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--color-text-muted)', marginLeft: 12 }}>SHA-256：{String(b.sha256).slice(0, 16)}…</div>}
                {invalid.length > 0 && <div style={{ fontSize: 10, color: 'var(--red)', marginLeft: 12 }}>无效引用：{invalid.join('，')}</div>}
              </div>
            );
          })}
        </div>
      );
    }
    // stage_plan
    if (kind === 'stage_plan' || j.artifact_type === 'stage_plan') {
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Stage Plan — {(j.stage as string || '').toUpperCase()}</div>
          {renderFieldCard('objective', j.objective)}{renderFieldCard('scope', j.scope)}
          {renderFieldCard('risk_level', j.risk_level)}{renderFieldCard('gate_policy', j.gate_policy)}
          {renderFieldCard('completion_criteria', j.completion_criteria)}
          {renderFieldCard('plan_summary', j.plan_summary)}{renderFieldCard('key_decisions', j.key_decisions)}
          {renderFieldCard('estimated_tasks', j.estimated_tasks)}{renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // task_graph
    if (kind === 'task_graph' || j.artifact_type === 'task_graph') {
      const nodeCount = j.node_count as number | undefined;
      return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>TaskGraph — {(j.stage as string || '').toUpperCase()}</div>
          {nodeCount !== undefined && nodeCount > 50
            ? <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>任务图共 {nodeCount} 节点 / {(j.edge_count as number) || 0} 条边（图较大，仅显示摘要）</div>
            : <>{renderFieldCard('node_count', j.node_count)}{renderFieldCard('edge_count', j.edge_count)}{renderFieldCard('degraded', j.degraded)}{renderFieldCard('milestones', j.milestones)}</>
          }
          {renderFieldCard('generated_at', j.generated_at)}
        </div>
      );
    }
    // generic field cards for all other JSON
    const skipKeys = new Set(['project_id', 'artifact_id']);
    return (
      <div>
        {!!j.stage && <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
          {FIELD_LABELS[j.artifact_type as string] || (j.artifact_type as string) || '报告'} — {(j.stage as string).toUpperCase()}
        </div>}
        {Object.entries(j).filter(([k]) => !skipKeys.has(k)).map(([k, v]) => renderFieldCard(k, v))}
      </div>
    );
  }

  // R17.3-6 WP-4（EG-WP4-1）：安全 Gate 专用风险面板。
  //  - desensitization_release：脱敏风险说明（path/pattern/count，无密钥明文）+ L5 标识 + 放行提示
  //  - action_approval（ACP 高风险）：命令请求详情（后端已脱敏）+ 高风险标识 + 需用户确认
  function renderSecurityPanel(): React.ReactNode {
    const risk = gate?.risk_level || (isDesensGate ? 'L5' : 'L4');
    const issues = Array.isArray(desensRisk?.issues) ? desensRisk.issues : [];
    return (
      <div>
        {/* 高风险标识 + 需用户确认提示（D-034：L5 高风险必须用户 Gate 确认） */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
          padding: '8px 12px', borderRadius: 6,
          background: 'var(--red-soft, #ffebee)', color: 'var(--red)',
        }}>
          <Icon name="blocked" size={18} />
          <div style={{ fontSize: 13, fontWeight: 600 }}>
            {isDesensGate ? '脱敏硬门禁：默认阻断交付' : '高风险动作：需用户确认后执行'}
          </div>
          <span style={{
            fontSize: 11, padding: '1px 7px', borderRadius: 3, marginLeft: 'auto',
            background: 'var(--red)', color: '#fff',
          }}>
            风险 {risk} · 需用户确认（D-034）
          </span>
        </div>

        {/* 触发原因 / 摘要（后端已脱敏，前端不反解） */}
        {(gate?.reason || gate?.summary) && (
          <div style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 12 }}>
            {gate?.reason || gate?.summary}
          </div>
        )}

        {isDesensGate && (
          <div>
            {desensLoading && <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>正在加载脱敏风险说明…</div>}
            {desensRisk ? (
              <div>
                {desensRisk.reason && <div style={{ fontSize: 12, marginBottom: 8 }}>{desensRisk.reason}</div>}
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                  疑似密钥/凭据命中（{desensRisk.issue_count ?? issues.length} 处 · 已脱敏：仅文件路径 / 命中模式 / 计数，无密钥明文）
                </div>
                {issues.length > 0 ? (
                  <div style={{ border: '1px solid var(--color-border)', borderRadius: 6, overflow: 'hidden' }}>
                    <div style={{ display: 'flex', fontSize: 11, fontWeight: 600, background: 'var(--color-surface-subtle)', padding: '6px 10px' }}>
                      <span style={{ flex: 1 }}>文件路径</span>
                      <span style={{ width: 170, flexShrink: 0 }}>命中模式</span>
                      <span style={{ width: 44, flexShrink: 0, textAlign: 'right' }}>计数</span>
                    </div>
                    {issues.map((it: any, i: number) => (
                      <div key={i} style={{ display: 'flex', fontSize: 11, padding: '6px 10px', borderTop: '1px solid var(--color-border)', fontFamily: 'var(--mono)' }}>
                        <span style={{ flex: 1, wordBreak: 'break-all' }}>{it.path}</span>
                        <span style={{ width: 170, flexShrink: 0, color: 'var(--color-text-muted)', wordBreak: 'break-all' }}>{it.pattern}</span>
                        <span style={{ width: 44, flexShrink: 0, textAlign: 'right' }}>{it.count}</span>
                      </div>
                    ))}
                  </div>
                ) : <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>无逐项明细。</div>}
                {desensRisk.release_path && (
                  <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>
                    放行路径：{desensRisk.release_path}
                  </div>
                )}
              </div>
            ) : (!desensLoading && (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                未能加载逐项脱敏明细（交付包可能已放行或暂不可用）；请参考上方摘要中的命中概览后决策。
              </div>
            ))}
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 12, padding: '8px 10px', background: 'var(--amber-soft, #fff8e1)', borderRadius: 6 }}>
              批准即表示你确认上述命中项不含真实敏感凭据（或已妥善处理），并授权放行交付。这是唯一放行路径（SEC-01 / D-032）。
            </div>
          </div>
        )}

        {isActionApproval && (
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', padding: '8px 10px', background: 'var(--amber-soft, #fff8e1)', borderRadius: 6 }}>
            外部编程 Agent 请求执行上述高风险命令（命令摘要已由后端脱敏）。批准将一次性授权本次执行（D-034）；拒绝或超时将保守阻断。
          </div>
        )}
      </div>
    );
  }

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
        {isSecurityGate ? renderSecurityPanel() : (
          <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
              请审阅下方阶段材料后决定（批准后进入下一阶段）。
            </div>
            <div style={{ display: 'flex', gap: 16, minHeight: 300 }}>
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
              <div style={{ flex: 1, overflow: 'auto', maxHeight: '50vh' }}>
                {!content && loadingMaterial && (
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>加载中…</div>
                )}
                {content === '// 文件不可用或尚未生成' && (
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>该材料尚未生成或不可用。</div>
                )}
                {content && activeMat?.type === 'json' && (
                  <div style={{ padding: '4px 0' }}>{renderJsonContent(content)}</div>
                )}
                {content && activeMat?.type === 'markdown' && (
                  <div style={{ padding: '4px 0' }}>{renderMarkdown(content)}</div>
                )}
                {!content && !loadingMaterial && (
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>点击左侧材料查看内容</div>
                )}
              </div>
            </div>
          </div>
        )}

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
          {isSecurityGate ? (
            <>
              <button className="btn sm" style={{ background: 'var(--green)', color: '#fff', fontSize: 12 }} disabled={deciding}
                onClick={() => handleDecision('approve')}>{isDesensGate ? '批准放行' : '批准执行'}</button>
              <button className="btn sm ghost" style={{ color: 'var(--red)', fontSize: 12 }} disabled={deciding}
                onClick={() => handleDecision('reject')}>拒绝</button>
            </>
          ) : (
            <>
              <button className="btn sm" style={{ background: 'var(--green)', color: '#fff', fontSize: 12 }} disabled={deciding}
                onClick={() => handleDecision('approve')}>批准</button>
              <button className="btn sm ghost" style={{ fontSize: 12 }} disabled={deciding}
                onClick={() => handleDecision('request_changes')}>请求修改</button>
              <button className="btn sm ghost" style={{ color: 'var(--red)', fontSize: 12 }} disabled={deciding}
                onClick={() => handleDecision('reject')}>拒绝</button>
            </>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" style={{ fontSize: 12 }} onClick={() => { setExpanded(false); setReworkHint(false); setDecisionFeedback(null); setReason(''); setReasonError(null); }}>
            关闭
          </button>
        </div>
      </Modal>
    </>
  );
}
