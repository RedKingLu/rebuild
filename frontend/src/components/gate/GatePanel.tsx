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

function getMaterialLabels(stage: string): MaterialItem[] {
  if (stage === 'p0') {
    return [
      { path: 'artifacts/intake_report.json', label: '阶段计划 (intake_report)', type: 'json' },
      { path: 'artifacts/p0_execution_record.json', label: '执行记录 (p0_execution)', type: 'json' },
      { path: 'artifacts/p0_construction_report.md', label: '施工报告 (p0_report)', type: 'markdown' },
      { path: 'artifacts/p0_review_pass.json', label: 'Review 报告 (p0_review)', type: 'json' },
    ];
  }
  // p1 (default for other stages)
  return [
    { path: 'artifacts/p1_stage_plan.json', label: '阶段计划 (p1_stage_plan)', type: 'json' },
    { path: 'artifacts/p1_execution_record.json', label: '执行记录 (p1_execution)', type: 'json' },
    { path: 'artifacts/p1_construction_report.md', label: '施工报告 (p1_report)', type: 'markdown' },
    { path: 'artifacts/p1_review_pass.json', label: 'Review 报告 (p1_review)', type: 'json' },
  ];
}

interface Props {
  gate: any;
  projectId: string;
  onDecided: () => void;
  onProfilingStart?: () => void;
}

export function GatePanel({ gate, projectId, onDecided, onProfilingStart }: Props) {
  const materialLabels = getMaterialLabels(gate?.stage || 'p0');
  const [expanded, setExpanded] = useState(false);
  const [activeMaterial, setActiveMaterial] = useState<string>('');
  const [materialContents, setMaterialContents] = useState<Record<string, string>>({});
  const [loadingMaterial, setLoadingMaterial] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [decisionFeedback, setDecisionFeedback] = useState<string | null>(null);
  const [reworkHint, setReworkHint] = useState(false);
  const [profiling, setProfiling] = useState(false);  // R9-3G-C: auto-trigger P1 profiling

  const gateId = gate?.gate_id;
  const gateLabel = gate?.gate_type === 'stage_promotion' ? '阶段晋级 Gate' : (gate?.gate_type || 'Gate');

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
    setDeciding(true);
    setDecisionFeedback(null);
    try {
      const runId = gate.run_id || 'unknown';
      const stage = gate.stage || 'p0';
      const resp = await fetch(`/api/projects/${projectId}/runs/${runId}/stages/${stage}/promotion-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reason: `User ${decision} via GatePanel` }),
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

      // R9-3G-C: Auto-trigger P1 profiling when P0 gate is approved
      if (decision === 'approve' && stage === 'p0') {
        setProfiling(true);
        setDecisionFeedback('P1 全量识别进行中…');
        setExpanded(false);      // close Modal — user watches in AgentChat
        onProfilingStart?.();    // switch to AgentChat tab
        try {
          const profileResp = await fetch(`/api/projects/${projectId}/profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          if (!profileResp.ok) {
            const errData = await profileResp.json().catch(() => ({}));
            setDecisionFeedback(`P1 识别失败: ${(errData as any).detail || profileResp.status}`);
          } else {
            setDecisionFeedback('P1 全量识别完成。');
          }
        } catch (e: any) {
          setDecisionFeedback(`P1 识别出错: ${e.message}`);
        } finally {
          setProfiling(false);
        }
      }

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
      }} onClick={() => setExpanded(true)}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>⚠ {gateLabel}</div>
          <div style={{ fontSize: 12, marginTop: 2 }}>{gate?.summary || gate?.reason}</div>
          {gate?.risk_level && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>风险级别: {gate.risk_level}</div>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>点击查看审核材料 →</div>
      </div>

      {/* Expanded Modal */}
      <Modal open={expanded} onClose={() => { setExpanded(false); setReworkHint(false); setDecisionFeedback(null); }}
        title={`Gate 审核 — ${gate?.stage?.toUpperCase?.() || 'P0'} 阶段晋级`} width={800}>
        <div style={{ display: 'flex', gap: 16, minHeight: 300 }}>
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

        {/* Feedback / Profiling status */}
        {decisionFeedback && (
          <div style={{
            marginTop: 12, padding: '8px 12px', borderRadius: 6, fontSize: 13,
            background: profiling ? 'var(--blue-soft, #e3f2fd)' :
                        reworkHint ? 'var(--amber-soft, #fff8e1)' : 'var(--red-soft, #ffebee)',
            color: profiling ? 'var(--color-primary)' :
                   reworkHint ? 'var(--amber-text, #8d6e00)' : 'var(--red)',
          }}>
            {profiling ? '⏳ ' : ''}{decisionFeedback}
          </div>
        )}

        {/* Bottom action bar */}
        <div style={{ display: 'flex', gap: 8, marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
          <button className="btn sm" style={{ background: 'var(--green)', color: '#fff', fontSize: 12 }} disabled={deciding || profiling}
            onClick={() => handleDecision('approve')}>批准</button>
          <button className="btn sm ghost" style={{ fontSize: 12 }} disabled={deciding || profiling}
            onClick={() => handleDecision('request_changes')}>请求修改</button>
          <button className="btn sm ghost" style={{ color: 'var(--red)', fontSize: 12 }} disabled={deciding || profiling}
            onClick={() => handleDecision('reject')}>拒绝</button>
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" style={{ fontSize: 12 }} onClick={() => { setExpanded(false); setReworkHint(false); setDecisionFeedback(null); }}>
            关闭
          </button>
        </div>
      </Modal>
    </>
  );
}
