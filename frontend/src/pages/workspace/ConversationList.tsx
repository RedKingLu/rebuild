/** UX-3: conversation history sidebar — lists past sessions grouped by stage, click to
 *  resume, ＋ to start a new one. Replaces the old "Agent 对话见中央常驻 Tab" placeholder. */
import { useState, useEffect } from 'react';
import { useWorkspaceStore } from '../../stores';
import {
  listConversations, createConversation, roleLabel,
  type Conversation,
} from '../../services/conversationService';

interface Props {
  projectId: string;
  stage: string;
  refreshKey: number;
  onSelect: (conv: Conversation) => void;
}

export function ConversationList({ projectId, stage, refreshKey, onSelect }: Props) {
  const activeConversationId = useWorkspaceStore(s => s.activeConversationId);
  const [convos, setConvos] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try { setConvos(await listConversations(projectId)); }
    catch { /* sidebar is best-effort */ }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [projectId, refreshKey]);

  const handleNew = async () => {
    // New conversation for the current stage's specialist.
    const role = activeConversationId
      ? (convos.find(c => c.conversation_id === activeConversationId)?.agent_role || 'node_worker')
      : 'node_worker';
    const created = await createConversation(projectId, stage, role);
    await load();
    onSelect(created);
  };

  // Group by stage (most recent first within group).
  const byStage: Record<string, Conversation[]> = {};
  for (const c of convos) {
    (byStage[c.stage] = byStage[c.stage] || []).push(c);
  }
  const stageOrder = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'gate'];
  const stages = Object.keys(byStage).sort((a, b) =>
    (stageOrder.indexOf(a) + 1 || 99) - (stageOrder.indexOf(b) + 1 || 99));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <b style={{ fontSize: 12 }}>对话历史</b>
        <button className="btn sm" onClick={handleNew} style={{ fontSize: 11, padding: '2px 8px' }}>＋ 新建</button>
      </div>
      {loading && <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>加载中…</div>}
      {!loading && convos.length === 0 && (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>暂无对话。发送消息即创建。</div>
      )}
      {stages.map(s => (
        <div key={s} style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 3, textTransform: 'uppercase' }}>
            {s}
          </div>
          {byStage[s].map(c => (
            <div key={c.conversation_id} onClick={() => onSelect(c)} style={{
              padding: '6px 8px', marginBottom: 3, borderRadius: 6, cursor: 'pointer',
              background: c.conversation_id === activeConversationId
                ? 'var(--color-primary-soft)' : 'var(--color-surface-subtle)',
              border: `1px solid ${c.conversation_id === activeConversationId ? 'var(--color-primary)' : 'var(--color-border)'}`,
            }}>
              <div style={{
                fontWeight: c.conversation_id === activeConversationId ? 600 : 400,
                color: c.conversation_id === activeConversationId ? 'var(--color-primary)' : 'var(--color-text)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{c.title || `${s.toUpperCase()} · ${roleLabel(c.agent_role)}`}</div>
              <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 2, display: 'flex', gap: 6 }}>
                <span>{roleLabel(c.agent_role)}</span>
                <span>·</span>
                <span>{c.message_count} 条</span>
                {c.last_message_at && <span>· {c.last_message_at.slice(5, 16)}</span>}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
