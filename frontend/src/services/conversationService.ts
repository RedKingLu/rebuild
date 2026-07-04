/** UX-3: conversation history API client. */

import { get, post, unwrap } from './client';

export interface Conversation {
  conversation_id: string;
  project_id: string;
  run_id: string;
  stage: string;
  agent_role: string;
  title: string;
  status: string;
  message_count: number;
  created_at: string;
  last_message_at: string | null;
}

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  role: 'user' | 'agent' | 'system' | 'tool';
  content: string;
  meta: Record<string, unknown>;
  created_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  node_worker: '执行 Agent',
  acceptance: '验收 Agent',
  conversation_gate: 'Gate Agent',
  auto_review: '审核 Agent',
  expert: '专家 Agent',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] || role;
}

export async function listConversations(projectId: string): Promise<Conversation[]> {
  const resp = await get<{ conversations: Conversation[]; total: number }>(
    `/projects/${projectId}/agent/conversations`,
  );
  return unwrap(resp).conversations;
}

export async function getConversationMessages(
  projectId: string, conversationId: string,
): Promise<{ conversation: Conversation; messages: ChatMessage[] }> {
  const resp = await get<{ conversation: Conversation; messages: ChatMessage[] }>(
    `/projects/${projectId}/agent/conversations/${conversationId}/messages`,
  );
  return unwrap(resp);
}

export async function createConversation(
  projectId: string, stage: string, agentRole: string, title?: string,
): Promise<Conversation> {
  const resp = await post<Conversation>(
    `/projects/${projectId}/agent/conversations`,
    { stage, agent_role: agentRole, title: title || '' },
  );
  return unwrap(resp);
}
