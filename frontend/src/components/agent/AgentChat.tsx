/** AgentChat — R9-3G-C: Streaming agent conversation component.
 *  SSE-based real-time chat with message list, input, and send.
 *  Reads execMode from useWorkspaceStore for mode-aware behavior (G-D).
 */
import { useState, useRef, useEffect } from 'react';
import { useWorkspaceStore, type ExecMode } from '../../stores';
import { decideGate } from '../../services/gateService';

interface ChatMessage {
  role: 'user' | 'agent';
  content: string;
  timestamp: string;
}

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
}

export function AgentChat({ projectId, stage, reviewEvents, systemMessages = [] }: Props) {
  const execMode = useWorkspaceStore(s => s.execMode);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState('');
  const [error, setError] = useState<string | null>(null);
  // R9-5-7 T12: real HITL — parked action awaiting the user's Gate decision.
  const [pendingGate, setPendingGate] = useState<PendingGate | null>(null);
  const [lastMessage, setLastMessage] = useState('');
  const [deciding, setDeciding] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, streamContent]);

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
        body: JSON.stringify({ message: text, mode: execMode, confirm }),
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
              // R9-5-7 T12: backend parked a controlled action behind a Gate.
              if (currentEvent === 'gate.request' && data.gate_id != null) {
                setPendingGate({
                  gate_id: data.gate_id,
                  action: data.action || '',
                  risk_level: data.risk_level || '',
                  summary: data.summary || '',
                });
              }
              if (currentEvent === 'done' || data.done) {
                if (fullContent) {
                  setMessages(prev => [...prev, {
                    role: 'agent',
                    content: fullContent,
                    timestamp: new Date().toISOString(),
                  }]);
                }
                setStreamContent('');
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
    }
  };

  // R9-5-7 T12: approve the parked action via the REAL Gate decision endpoint,
  // then resume the agent run with confirm=true (no fake chat bubble — STOP-4).
  const handleApproveGate = async () => {
    if (!pendingGate || deciding) return;
    setDeciding(true);
    try {
      await decideGate(projectId, pendingGate.gate_id, 'approve', 'user approved action');
      setMessages(prev => [...prev, { role: 'user', content: `✓ 已确认执行：${pendingGate.action}`, timestamp: new Date().toISOString() }]);
      setPendingGate(null);
      await sendMessage(lastMessage, true);
    } catch (e: any) {
      setError(e.message || '确认失败');
    } finally {
      setDeciding(false);
    }
  };

  const handleRejectGate = async () => {
    if (!pendingGate || deciding) return;
    setDeciding(true);
    try {
      await decideGate(projectId, pendingGate.gate_id, 'reject', 'user rejected action');
      setMessages(prev => [...prev, { role: 'user', content: `✕ 已拒绝：${pendingGate.action}`, timestamp: new Date().toISOString() }]);
      setPendingGate(null);
    } catch (e: any) {
      setError(e.message || '拒绝失败');
    } finally {
      setDeciding(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setMessages(prev => [...prev, { role: 'user', content: text, timestamp: new Date().toISOString() }]);
    setInput('');
    await sendMessage(text);
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 13 }}>
      {/* Mode indicator */}
      <div style={{
        padding: '6px 12px', background: 'var(--color-surface-subtle)',
        borderBottom: '1px solid var(--color-border)', fontSize: 11,
        color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span>模式: <b style={{ color: 'var(--color-primary)' }}>{modeLabel[execMode]}</b></span>
        <span>|</span>
        <span>阶段: {stage.toUpperCase()}</span>
        <span style={{ marginLeft: 'auto' }}>Agent 对话</span>
      </div>

      {/* Message list */}
      <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {messages.length === 0 && !streaming && systemMessages.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 24, fontSize: 13 }}>
            欢迎使用 Agent 对话。你可以询问项目信息、技术栈、阶段进度等。
          </div>
        )}

        {/* System messages (Agent execution events, P0-2 fix) */}
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

        {messages.map((m, i) => (
          <div key={i} style={{
            marginBottom: 10,
            display: 'flex',
            justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '80%',
              padding: '8px 12px',
              borderRadius: 8,
              background: m.role === 'user'
                ? 'var(--color-primary-soft)'
                : 'var(--color-surface-subtle)',
              border: '1px solid var(--color-border)',
            }}>
              <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                {m.role === 'user' ? '你' : 'Agent'} · {m.timestamp.slice(11, 19) || ''}
              </div>
              <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</div>
            </div>
          </div>
        ))}

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
        {execMode === 'auto' && !pendingGate && messages.length > 0 && messages[messages.length - 1].role === 'agent' && !streaming && (
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
