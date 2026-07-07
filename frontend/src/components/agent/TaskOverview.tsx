/** UX-4 (corrected) / UX-5: the agent task-overview top bar.
 *
 * Shows what the agent is CURRENTLY working on — NOT the 7-stage project pipeline.
 * Three honest layers, in priority order (only rendered when the data is real):
 *
 *   1. TaskGraph node checklist — if an active TaskGraph run exists for the current
 *      (run, stage), show each node with its real runtime status (pending/running/
 *      completed/blocked/...). Fetched from GET .../stages/{stage}/taskgraph.
 *   2. Current task context — the active conversation's goal: specialist agent + the
 *      latest user request (the thing the agent is working on right now).
 *   3. Real-time status — derived from the live stream: idle / thinking / calling
 *      tool N / waiting on a gate / done.
 *
 * If there is no active TaskGraph, we honestly fall back to (2)+(3) — we never
 * fabricate a task list. */

import { useState, useEffect } from 'react';

interface GraphNode {
  node_id: string;
  node_type: string;
  title: string;
  status: string;
  retry_count: number;
}

interface TaskGraphData {
  graph_id: string | null;
  graph_run_id: string | null;
  graph_status: string | null;
  nodes: GraphNode[];
}

const NODE_STATUS_STYLE: Record<string, { icon: string; color: string; label: string }> = {
  completed: { icon: '✅', color: 'var(--green)', label: '完成' },
  running: { icon: '🔄', color: 'var(--color-primary)', label: '执行中' },
  in_progress: { icon: '🔄', color: 'var(--color-primary)', label: '执行中' },
  waiting_gate: { icon: '⏸', color: 'var(--amber)', label: '待决策' },
  blocked: { icon: '✕', color: 'var(--red)', label: '阻塞' },
  failed: { icon: '❌', color: 'var(--red)', label: '失败' },
  rework_required: { icon: '↺', color: 'var(--amber)', label: '需返工' },
  pending: { icon: '○', color: 'var(--color-text-muted)', label: '待执行' },
  skipped: { icon: '–', color: 'var(--color-text-muted)', label: '跳过' },
};

export interface TaskOverviewStatus {
  phase: 'idle' | 'thinking' | 'tool' | 'gate' | 'done';
  toolName?: string;
  toolIndex?: number;
  toolTotal?: number;
}

interface Props {
  projectId: string;
  runId?: string;
  stage: string;
  agentRole?: string;
  latestRequest?: string;
  status: TaskOverviewStatus;
  /** R17-3: 当前 active gate（用于 plan_review 等待态显示"计划审核中"） */
  activeGate?: { gate_type: string; gate_status: string; stage: string } | null;
}

export function TaskOverview({ projectId, runId, stage, agentRole, latestRequest, status, activeGate }: Props) {
  const [graph, setGraph] = useState<TaskGraphData | null>(null);

  useEffect(() => {
    let cancelled = false;
    setGraph(null);
    if (!runId || !stage) return;
    (async () => {
      try {
        const resp = await fetch(
          `/api/projects/${projectId}/runs/${runId}/stages/${stage}/taskgraph`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (cancelled) return;
        const d = data.data || data;
        setGraph({ graph_id: d.graph_id, graph_run_id: d.graph_run_id,
                   graph_status: d.graph_status, nodes: Array.isArray(d.nodes) ? d.nodes : [] });
      } catch {
        // no active graph — fall back to task-context (no nodes shown)
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, runId, stage]);

  const roleLabel: Record<string, string> = {
    node_worker: '执行 Agent', acceptance: '验收 Agent', conversation_gate: 'Gate Agent',
    auto_review: '审核 Agent', expert: '专家 Agent',
  };

  // R17-3: plan_review gate 等待态优先于 agent 自身的 status.phase
  const isPlanReviewPending = activeGate?.gate_type === 'plan_review' && activeGate?.gate_status === 'waiting_decision';

  const statusLine = () => {
    if (isPlanReviewPending) return { icon: '⏸', text: '等待计划审核', color: 'var(--amber)' };
    switch (status.phase) {
      case 'thinking': return { icon: '💭', text: '思考中…', color: 'var(--color-primary)' };
      case 'tool': return { icon: '🔧', text: `调用工具 ${status.toolName || ''}${status.toolTotal ? ` (${status.toolIndex}/${status.toolTotal})` : ''}`, color: 'var(--color-primary)' };
      case 'gate': return { icon: '⏸', text: '等待 Gate 决策…', color: 'var(--amber)' };
      case 'done': return { icon: '✅', text: '本轮完成', color: 'var(--green)' };
      default: return { icon: '○', text: '空闲', color: 'var(--color-text-muted)' };
    }
  };
  const st = statusLine();

  const hasGraph = graph && graph.nodes.length > 0;
  const doneCount = hasGraph ? graph!.nodes.filter(n => n.status === 'completed' || n.status === 'skipped').length : 0;

  return (
    <div style={{
      background: 'var(--color-surface-subtle)', borderBottom: '1px solid var(--color-border)',
      flexShrink: 0, fontSize: 12,
    }}>
      {/* Layer 1: active TaskGraph node checklist (real data only) */}
      {hasGraph && (
        <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--color-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 600 }}>任务清单</span>
            <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
              {stage.toUpperCase()} · {doneCount}/{graph!.nodes.length} 完成
            </span>
            {graph!.graph_status && (
              <span style={{ fontSize: 10, marginLeft: 'auto', color: 'var(--color-text-muted)' }}>
                图状态: {graph!.graph_status}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {graph!.nodes.map((n) => {
              const ns = NODE_STATUS_STYLE[n.status] || NODE_STATUS_STYLE.pending;
              return (
                <div key={n.node_id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                  <span style={{ fontSize: 11, flexShrink: 0 }}>{ns.icon}</span>
                  <span style={{
                    color: ns.color, fontWeight: n.status === 'running' || n.status === 'in_progress' ? 600 : 400,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
                  }}>{n.title}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-text-muted)', flexShrink: 0 }}>
                    {ns.label}{n.retry_count > 0 ? ` · 重试${n.retry_count}` : ''}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Layer 2+3: current task context + real-time status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px' }}>
        <span style={{ fontSize: 10, color: 'var(--color-text-muted)', flexShrink: 0 }}>当前任务</span>
        {agentRole && (
          <span style={{ fontSize: 10, color: 'var(--color-primary)', background: 'var(--color-primary-soft)', padding: '1px 6px', borderRadius: 3, flexShrink: 0 }}>
            {roleLabel[agentRole] || agentRole}
          </span>
        )}
        <span style={{
          flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontSize: 12, color: latestRequest ? 'var(--color-text)' : 'var(--color-text-muted)',
        }}>
          {latestRequest || (isPlanReviewPending ? `${(activeGate?.stage || stage).toUpperCase()} 计划审核中…` : (hasGraph ? '执行任务清单' : '暂无进行中的任务'))}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0, fontSize: 11, color: st.color }}>
          <span>{st.icon}</span><span>{st.text}</span>
        </span>
      </div>
    </div>
  );
}
