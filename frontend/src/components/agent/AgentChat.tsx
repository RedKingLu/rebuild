/** AgentChat — R9-3G-C: Streaming agent conversation component.
 *  SSE-based real-time chat with message list, input, and send.
 *  Reads execMode from useWorkspaceStore for mode-aware behavior (G-D).
 */
import { useState, useRef, useEffect } from 'react';
import { useWorkspaceStore, type ExecMode } from '../../stores';
import { decideGate } from '../../services/gateService';
import { getConversationMessages, type Conversation } from '../../services/conversationService';
import { fetchFileContent } from '../../services/workspaceService';
import { TaskOverview, type TaskOverviewStatus } from './TaskOverview';

/** R9-5-7 T12: a controlled action parked behind a real action_approval Gate,
 *  surfaced by the backend `gate.request` SSE event. */
interface PendingGate {
  gate_id: string;
  action: string;
  risk_level: string;
  summary: string;
}

export interface ReviewEvent {
  passed: boolean;
  escalated?: boolean;
  issues?: string[];
  round?: number;
  maxRounds?: number;
  escalationReason?: string;
}

export interface SystemMessage {
  id: string;
  phase: string;
  message: string;
  ok?: boolean;
  file_count?: number;
  gate_id?: string;
  timestamp: string;
}

interface Props {
  projectId: string;
  stage: string;
  reviewEvents?: ReviewEvent[];
  systemMessages?: SystemMessage[];
  // UX-3: when set, load this conversation's history instead of starting fresh.
  conversationId?: string | null;
  onConversationChange?: (conv: Conversation | null) => void;
  // UX-4: real run state for the task-overview bar (genuine progress, never fabricated).
  // runId fetches the active TaskGraph; if omitted, the bar shows task-context + live status.
  runId?: string | null;
  runStatus?: string;
  // R17-X: 当前 active gate（plan_presentation 计划审核等待态 → TaskOverview 显示"计划审核中"；原 plan_review 欢迎门已删除）
  activeGate?: { gate_type: string; gate_status: string; stage: string } | null;
}

/** UX-4: a timeline entry. user/agent are chat bubbles; tool is a structured
 *  execution block (🔧 tool name + result) rendered between the thinking & reply. */
interface DisplayEntry {
  id: string;
  kind: 'user' | 'agent' | 'tool';
  content: string;
  toolName?: string;
  timestamp: string;
}

export function AgentChat({ projectId, stage, reviewEvents, systemMessages = [],
  conversationId, onConversationChange,
  runId, runStatus, activeGate }: Props) {
  const execMode = useWorkspaceStore(s => s.execMode);
  const [entries, setEntries] = useState<DisplayEntry[]>([]);
  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState('');
  const [error, setError] = useState<string | null>(null);
  // R9-5-7 T12: real HITL — parked action awaiting the user's Gate decision.
  const [pendingGate, setPendingGate] = useState<PendingGate | null>(null);
  const [lastMessage, setLastMessage] = useState('');
  const [deciding, setDeciding] = useState(false);
  // UX-5: reason for the action-gate decision (required on reject).
  const [gateReason, setGateReason] = useState('');
  const [gateReasonError, setGateReasonError] = useState<string | null>(null);
  // UX-5: rework requirements surfaced for the Acceptance/rework agent.
  const [reworkNotes, setReworkNotes] = useState<Array<{ round: number; decision: string; reason: string; at: string }>>([]);
  const listRef = useRef<HTMLDivElement>(null);

  // UX-4: live tool events for the current streaming turn (rendered as execution blocks).
  const [liveTools, setLiveTools] = useState<Array<{ tool: string; result: string }>>([]);
  const liveToolsRef = useRef<Array<{ tool: string; result: string }>>([]);
  // UX-4: latest user request = the current task the agent is working on.
  const [latestRequest, setLatestRequest] = useState('');
  // UX-4: real-time execution status derived from the live stream.
  const [taskStatus, setTaskStatus] = useState<TaskOverviewStatus>({ phase: 'idle' });

  // Auto-scroll on new entries
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [entries, streamContent, liveTools]);

  // UX-3: load conversation history when the active conversation changes.
  useEffect(() => {
    let cancelled = false;
    if (!conversationId) { setCurrentConversation(null); onConversationChange?.(null); return; }
    (async () => {
      try {
        const { conversation, messages: hist } = await getConversationMessages(projectId, conversationId);
        if (cancelled) return;
        setCurrentConversation(conversation);
        // UX-4: rebuild the timeline including persisted tool-call execution blocks.
        const loaded: DisplayEntry[] = [];
        for (const m of hist) {
          if (m.role === 'user' || m.role === 'agent') {
            loaded.push({
              id: m.message_id || `${m.role}-${m.created_at}`,
              kind: m.role as 'user' | 'agent',
              content: m.content,
              timestamp: m.created_at,
            });
          } else if (m.role === 'tool') {
            loaded.push({
              id: m.message_id || `tool-${m.created_at}`,
              kind: 'tool',
              content: m.content,
              toolName: (m.meta && (m.meta as any).tool) || undefined,
              timestamp: m.created_at,
            });
          }
        }
        setEntries(loaded);
        onConversationChange?.(conversation);
      } catch {
        if (!cancelled) { setCurrentConversation(null); onConversationChange?.(null); }
      }
    })();
    return () => { cancelled = true; };
  }, [conversationId, projectId]);

  // UX-5: when this is the Acceptance/rework conversation, fetch the persisted rework notes
  // (artifacts/{stage}_rework_notes.json) so the agent — and the user — see why prior
  // attempts were rejected and what to fix.
  useEffect(() => {
    let cancelled = false;
    setReworkNotes([]);
    if (!conversationId || !currentConversation) return;
    const role = currentConversation.agent_role;
    if (role !== 'acceptance' && role !== 'conversation_gate') return;
    (async () => {
      try {
        const fc = await fetchFileContent(projectId, `artifacts/${currentConversation.stage}_rework_notes.json`);
        if (cancelled) return;
        const data = JSON.parse(fc.content || '[]');
        if (Array.isArray(data)) setReworkNotes(data);
      } catch {
        /* no notes yet (first attempt) — not an error */
      }
    })();
    return () => { cancelled = true; };
  }, [conversationId, currentConversation, projectId]);

  const sendMessage = async (text: string, confirm = false) => {
    if (streaming) return;
    setStreaming(true);
    setStreamContent('');
    setError(null);
    if (!confirm) setLastMessage(text);
    try {
      const resp = await fetch(`/api/projects/${projectId}/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text, mode: execMode, confirm,
          // UX-3: bind the message to the active persisted conversation.
          conversation_id: conversationId || undefined,
        }),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error((errData as any).detail || `HTTP ${resp.status}`);
      }

      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let fullContent = '';
      let buffer = '';
      let currentEvent = '';
      // UX-4: collect tool calls during this turn to render as execution blocks.
      const turnTools: Array<{ tool: string; result: string }> = [];
      liveToolsRef.current = [];
      setLiveTools([]);
      setTaskStatus({ phase: 'thinking' });

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (currentEvent === 'delta' && data.token) {
                fullContent += data.token;
                setStreamContent(fullContent);
              }
              // UX-4: tool execution event — capture name + result for the process view.
              if (currentEvent === 'tool' && data.tool) {
                const ev = { tool: data.tool, result: String(data.result || '') };
                turnTools.push(ev);
                liveToolsRef.current = [...liveToolsRef.current, ev];
                setLiveTools(liveToolsRef.current);
                setTaskStatus({ phase: 'tool', toolName: data.tool,
                                toolIndex: liveToolsRef.current.length,
                                toolTotal: liveToolsRef.current.length });
              }
              // UX-3: backend tells us which conversation this stream belongs to.
              if (currentEvent === 'conversation' && data.conversation_id) {
                useWorkspaceStore.getState().setActiveConversation(data.conversation_id);
              }
              // R9-5-7 T12: backend parked a controlled action behind a Gate.
              if (currentEvent === 'gate.request' && data.gate_id != null) {
                setPendingGate({
                  gate_id: data.gate_id,
                  action: data.action || '',
                  risk_level: data.risk_level || '',
                  summary: data.summary || '',
                });
                // UX-5: fresh gate → clear any previous reason.
                setGateReason('');
                setGateReasonError(null);
                setTaskStatus({ phase: 'gate' });
              }
              if (currentEvent === 'done' || data.done) {
                // UX-4: commit the agent reply + its tool-call execution blocks.
                setEntries(prev => {
                  const next = [...prev];
                  for (const t of turnTools) {
                    next.push({
                      id: `tool-${next.length}-${t.tool}`,
                      kind: 'tool',
                      content: t.result,
                      toolName: t.tool,
                      timestamp: new Date().toISOString(),
                    });
                  }
                  if (fullContent) {
                    next.push({
                      id: `agent-${Date.now()}`,
                      kind: 'agent',
                      content: fullContent,
                      timestamp: new Date().toISOString(),
                    });
                  }
                  return next;
                });
                setStreamContent('');
                setLiveTools([]);
                liveToolsRef.current = [];
                setTaskStatus({ phase: data.gate_pending ? 'gate' : 'done',
                                toolTotal: turnTools.length });
                setStreaming(false);
              }
            } catch {
              // Skip malformed SSE lines
            }
          }
        }
      }
    } catch (e: any) {
      setError(e.message);
      setStreaming(false);
      setStreamContent('');
      setTaskStatus({ phase: 'idle' });
    }
  };

  // R9-5-7 T12: approve the parked action via the REAL Gate decision endpoint,
  // then resume the agent run with confirm=true (no fake chat bubble — STOP-4).
  const handleApproveGate = async () => {
    if (!pendingGate || deciding) return;
    setDeciding(true);
    setGateReasonError(null);
    try {
      // UX-5: pass the real user reason (optional for approve).
      await decideGate(projectId, pendingGate.gate_id, 'approve', gateReason.trim() || 'user approved action');
      setEntries(prev => [...prev, { id: `gate-ok-${Date.now()}`, kind: 'user', content: `✓ 已确认执行：${pendingGate.action}`, timestamp: new Date().toISOString() }]);
      setPendingGate(null);
      setGateReason('');
      await sendMessage(lastMessage, true);
    } catch (e: any) {
      setError(e.message || '确认失败');
    } finally {
      setDeciding(false);
    }
  };

  const handleRejectGate = async () => {
    if (!pendingGate || deciding) return;
    // UX-5: reject requires a reason — no dead-end rejections.
    if (!gateReason.trim()) {
      setGateReasonError('拒绝动作必须填写原因');
      return;
    }
    setDeciding(true);
    setGateReasonError(null);
    try {
      await decideGate(projectId, pendingGate.gate_id, 'reject', gateReason.trim());
      setEntries(prev => [...prev, { id: `gate-no-${Date.now()}`, kind: 'user', content: `✕ 已拒绝：${pendingGate.action}（${gateReason.trim()}）`, timestamp: new Date().toISOString() }]);
      setPendingGate(null);
      setGateReason('');
    } catch (e: any) {
      setError(e.message || '拒绝失败');
    } finally {
      setDeciding(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    // UX-5: on the first turn of a rework (Acceptance) conversation, inject the prior
    // rejection reasons into the message so the agent knows what to fix.
    let finalText = text;
    const isReworkStart = reworkNotes.length > 0 && currentConversation
      && currentConversation.agent_role === 'acceptance'
      && !entries.some(e => e.kind === 'user');
    if (isReworkStart) {
      const ctx = reworkNotes.map((n) => `第${n.round}轮${n.decision === 'reject' ? '拒绝' : '请求修改'}：${n.reason}`).join('\n');
      finalText = `【历史返工要求（请据此修改后重新提交）】\n${ctx}\n\n【当前用户指令】\n${text}`;
    }
    setLatestRequest(text);
    setTaskStatus({ phase: 'thinking' });
    setEntries(prev => [...prev, {
      id: `user-${Date.now()}`, kind: 'user', content: text, timestamp: new Date().toISOString(),
    }]);
    setInput('');
    await sendMessage(finalText);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const modeLabel: Record<ExecMode, string> = {
    manual: '手动',
    plan: '计划确认',
    auto: '自动',
  };

  // UX-4: a tool/result execution block.
  const renderToolBlock = (t: { tool: string; result: string }, key: string, live = false) => (
    <div key={key} style={{
      marginBottom: 6, borderRadius: 6, fontSize: 12,
      border: '1px solid var(--color-border)',
      background: live ? 'var(--blue-soft, #e3f2fd)' : 'var(--color-surface-subtle)',
      borderLeft: `3px solid ${live ? 'var(--color-primary)' : 'var(--amber)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px' }}>
        <span>{live ? '🔄' : '🔧'}</span>
        <code style={{ fontSize: 11, fontWeight: 600 }}>{t.tool}</code>
        <span style={{ fontSize: 10, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>工具调用</span>
      </div>
      <div style={{ padding: '0 10px 8px', maxWidth: '100%' }}>
        <pre style={{
          margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          background: 'var(--color-surface)', borderRadius: 4, padding: 6,
          border: '1px solid var(--color-border)', maxHeight: 120, overflow: 'auto',
          fontFamily: 'var(--mono)',
        }}>{t.result || '(无输出)'}</pre>
      </div>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 13 }}>
      {/* UX-4 (corrected): top task overview — what the agent is CURRENTLY working on:
          active TaskGraph node checklist (real) else task-context + live real-time status.
          This is NOT the 7-stage project pipeline. */}
      <TaskOverview projectId={projectId} runId={runId || undefined}
        stage={stage} agentRole={currentConversation?.agent_role || undefined}
        latestRequest={latestRequest} status={taskStatus}
        activeGate={activeGate} />

      {/* Mode + specialist indicator */}
      <div style={{
        padding: '5px 12px', background: 'var(--color-surface-subtle)',
        borderBottom: '1px solid var(--color-border)', fontSize: 11,
        color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span>模式: <b style={{ color: 'var(--color-primary)' }}>{modeLabel[execMode]}</b></span>
        <span>|</span>
        <span>阶段: {stage.toUpperCase()}</span>
        <span style={{ marginLeft: 'auto', fontSize: 10 }}>
          {runStatus ? `运行: ${runStatus}` : 'Agent 对话'}
        </span>
      </div>

      {/* UX-4: execution timeline — system msgs + chat bubbles + tool blocks + live stream */}
      <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {entries.length === 0 && !streaming && systemMessages.length === 0 && liveTools.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 24, fontSize: 13 }}>
            欢迎使用 Agent。你可以询问项目信息、技术栈、阶段进度，或要求执行动作。
          </div>
        )}

        {/* System messages (Agent execution events) */}
        {systemMessages.map((sm) => (
          <div key={sm.id} style={{
            marginBottom: 6, padding: '8px 12px', borderRadius: 6, fontSize: 12,
            background: sm.phase === 'complete' ? 'var(--green-soft, #e8f5e9)' :
                        sm.ok === false ? 'var(--red-soft, #ffebee)' : 'var(--blue-soft, #e3f2fd)',
            border: `1px solid ${sm.phase === 'complete' ? 'var(--green)' :
                                   sm.ok === false ? 'var(--red)' : 'var(--color-primary)'}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>{sm.phase === 'complete' ? '✅' : sm.ok === false ? '❌' : '🔄'}</span>
              <span style={{ fontWeight: 600 }}>{sm.message}</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 2 }}>
              {sm.timestamp.slice(11, 19)}
            </div>
          </div>
        ))}

        {/* UX-5: rework requirements — why prior attempts were rejected (Acceptance agent) */}
        {reworkNotes.length > 0 && (
          <div style={{
            marginBottom: 12, borderRadius: 6, fontSize: 12,
            border: '1px solid var(--amber)', borderLeft: '3px solid var(--amber)',
            background: 'var(--amber-soft, #fff8e1)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px' }}>
              <span>📋</span>
              <span style={{ fontWeight: 600, color: 'var(--amber-text, #8d6e00)' }}>
                返工要求（{reworkNotes.length} 次决策反馈）
              </span>
              <span style={{ fontSize: 10, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
                请据此修改后重新提交
              </span>
            </div>
            <div style={{ padding: '0 10px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {reworkNotes.map((n) => (
                <div key={n.round} style={{
                  background: 'var(--color-surface)', borderRadius: 4, padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                }}>
                  <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 2 }}>
                    第 {n.round} 轮 · {n.decision === 'reject' ? '拒绝' : '请求修改'} · {n.at ? n.at.slice(0, 16) : ''}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--color-text)' }}>{n.reason}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* UX-4 timeline entries: user/agent bubbles + tool execution blocks */}
        {entries.map((m) => {
          if (m.kind === 'tool') {
            return renderToolBlock({ tool: m.toolName || 'tool', result: m.content }, m.id);
          }
          return (
            <div key={m.id} style={{
              marginBottom: 10,
              display: 'flex',
              justifyContent: m.kind === 'user' ? 'flex-end' : 'flex-start',
            }}>
              <div style={{
                maxWidth: '80%',
                padding: '8px 12px',
                borderRadius: 8,
                background: m.kind === 'user'
                  ? 'var(--color-primary-soft)'
                  : 'var(--color-surface-subtle)',
                border: '1px solid var(--color-border)',
              }}>
                <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                  {m.kind === 'user' ? '你' : 'Agent'} · {m.timestamp ? m.timestamp.slice(11, 19) : ''}
                </div>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</div>
              </div>
            </div>
          );
        })}

        {/* UX-4: live tool calls during the current streaming turn */}
        {liveTools.map((t, i) => renderToolBlock(t, `live-${i}-${t.tool}`, true))}

        {/* Streaming content */}
        {/* R9-3G-D: Review Pass event cards */}
        {(reviewEvents || []).map((evt, i) => (
          <div key={`review-${i}`} style={{
            marginBottom: 8, padding: '8px 12px', borderRadius: 6, fontSize: 12,
            background: evt.escalated ? 'var(--red-soft, #ffebee)' :
                        evt.passed ? 'var(--green-soft, #e8f5e9)' : 'var(--amber-soft, #fff8e1)',
            border: `1px solid ${evt.escalated ? 'var(--red)' : evt.passed ? 'var(--green)' : 'var(--amber)'}`,
          }}>
            <div style={{ fontWeight: 600 }}>
              {evt.escalated ? '🔴 Review 已升级 — 需人工介入' :
               evt.passed ? '✅ Review Pass' : '⚠ Review 未通过'}
              {evt.round != null && evt.maxRounds != null && (
                <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                  第 {evt.round}/{evt.maxRounds} 轮
                </span>
              )}
            </div>
            {evt.escalated && evt.escalationReason && (
              <div style={{ marginTop: 4, fontSize: 11 }}>原因: {evt.escalationReason}</div>
            )}
            {!evt.passed && !evt.escalated && evt.issues && evt.issues.length > 0 && (
              <div style={{ marginTop: 4, fontSize: 11, color: 'var(--color-text)' }}>
                问题: {evt.issues.map((issue: any) => typeof issue === 'string' ? issue : issue.detail || '').join('; ')}
              </div>
            )}
            {!evt.passed && !evt.escalated && (
              <div style={{ marginTop: 4, fontSize: 11, color: 'var(--color-text-muted)' }}>
                正在返工…
              </div>
            )}
          </div>
        ))}

        {streaming && (
          <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'flex-start' }}>
            <div style={{
              maxWidth: '80%', padding: '8px 12px', borderRadius: 8,
              background: 'var(--color-surface-subtle)', border: '1px solid var(--color-primary)',
            }}>
              <div style={{ fontSize: 10, color: 'var(--color-primary)', marginBottom: 4 }}>
                Agent · 回复中…
                {execMode === 'plan' && <span style={{ marginLeft: 6, fontSize: 10, background: 'var(--color-primary-soft)', padding: '1px 6px', borderRadius: 3 }}>计划生成中</span>}
                {execMode === 'auto' && <span style={{ marginLeft: 6, fontSize: 10, background: 'var(--green-soft, #e8f5e9)', padding: '1px 6px', borderRadius: 3, color: 'var(--green)' }}>自动执行</span>}
              </div>
              <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {streamContent || '...'}
                <span style={{ animation: 'blink 1s infinite', color: 'var(--color-primary)' }}>|</span>
              </div>
            </div>
          </div>
        )}

        {/* R9-5-7 T12: real HITL — a controlled action is parked behind a Gate.
            Confirm/Reject call the REAL Gate decision endpoint (no fake bubble). */}
        {pendingGate && !streaming && (
          <div style={{
            marginBottom: 10, padding: '10px 12px', borderRadius: 6, fontSize: 12,
            background: 'var(--amber-soft, #fff8e1)', border: '1px solid var(--amber)',
          }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              ⏸ 待确认动作：{pendingGate.action} <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>（{pendingGate.risk_level}）</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-text)', marginBottom: 8 }}>{pendingGate.summary}</div>
            {/* UX-5: reason input — required when rejecting a controlled action */}
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 3 }}>
                决策原因 <span style={{ color: 'var(--red)' }}>*</span>
                <span style={{ fontSize: 10, marginLeft: 4 }}>（拒绝必填）</span>
              </div>
              <textarea
                value={gateReason}
                onChange={e => { setGateReason(e.target.value); if (gateReasonError) setGateReasonError(null); }}
                placeholder="例如：该动作风险过高，建议改用只读方式获取信息…"
                rows={2}
                style={{
                  width: '100%', padding: '6px 8px',
                  border: `1px solid ${gateReasonError ? 'var(--red)' : 'var(--color-border)'}`,
                  borderRadius: 4, fontSize: 11, resize: 'vertical', background: 'var(--color-surface)',
                  color: 'var(--color-text)', fontFamily: 'inherit', boxSizing: 'border-box',
                }}
              />
              {gateReasonError && <div style={{ fontSize: 10, color: 'var(--red)', marginTop: 3 }}>{gateReasonError}</div>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm" style={{ background: 'var(--green)', color: '#fff', fontSize: 11 }}
                disabled={deciding} onClick={handleApproveGate}>
                {deciding ? '处理中…' : '确认执行'}
              </button>
              <button className="btn sm ghost" style={{ color: 'var(--red)', fontSize: 11 }}
                disabled={deciding} onClick={handleRejectGate}>
                拒绝
              </button>
            </div>
          </div>
        )}

        {/* R9-3G-D: Auto mode indicator when agent auto-executes */}
        {execMode === 'auto' && !pendingGate && entries.length > 0 && entries[entries.length - 1].kind === 'agent' && !streaming && (
          <div style={{ fontSize: 10, color: 'var(--green)', paddingLeft: 12, marginBottom: 6 }}>
            ✓ 自动执行完成
          </div>
        )}

        {error && (
          <div style={{
            padding: '8px 12px', background: 'var(--red-soft, #ffebee)',
            color: 'var(--red)', borderRadius: 6, fontSize: 12, marginBottom: 10,
          }}>
            错误: {error}
            <button style={{ marginLeft: 8, fontSize: 11 }} onClick={() => setError(null)}>✕</button>
          </div>
        )}
      </div>

      {/* Input area */}
      <div style={{
        padding: '8px 12px', borderTop: '1px solid var(--color-border)',
        display: 'flex', gap: 8, alignItems: 'flex-end',
        background: 'var(--color-surface)',
      }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息，Enter 发送…"
          disabled={streaming}
          rows={2}
          style={{
            flex: 1, padding: '8px 10px', border: '1px solid var(--color-border)',
            borderRadius: 6, fontSize: 13, resize: 'none',
            background: 'var(--color-surface)',
            color: 'var(--color-text)',
            fontFamily: 'inherit',
          }}
        />
        <button
          className="btn sm"
          onClick={handleSend}
          disabled={streaming || !input.trim()}
          style={{ padding: '8px 16px', height: 'fit-content' }}
        >
          {streaming ? '回复中…' : '发送'}
        </button>
      </div>
    </div>
  );
}
