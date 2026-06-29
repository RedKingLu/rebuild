/** SSE Event service — connects to R4 mock event stream. */

// R4 最小联调（C-B）：同源相对路径，经 Vite dev proxy 转发到后端 SSE 端点。
// 可用 VITE_API_BASE 覆盖（需与 client.ts 保持一致）。
const API_BASE = (import.meta.env?.VITE_API_BASE as string | undefined) || '/api';
const SSE_URL = `${API_BASE}/events/stream`;

export function connectEventStream(
  projectId: string | null,
  onEvent: (event: Record<string, unknown>) => void,
  onError?: (err: Event) => void,
): EventSource {
  const url = projectId ? `${SSE_URL}?project_id=${projectId}` : SSE_URL;
  const es = new EventSource(url);

  // Listen to all event types
  const eventTypes = [
    'snapshot', 'heartbeat',
    'run.status_changed', 'stage.status_changed',
    'gate.created', 'gate.decided',
    'task_graph.status_changed',
    'checkpoint.created', 'interrupt.raised', 'resume.completed',
    'trace.written', 'audit.written',
    'error.occurred',
  ];

  for (const eventType of eventTypes) {
    es.addEventListener(eventType, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        // Inject the SSE event name so consumers can filter by type (R9-5-8 T6).
        onEvent({ event_type: eventType, ...data });
      } catch {
        // ignore parse errors
      }
    });
  }

  // Generic message handler as fallback
  es.onmessage = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
    } catch {
      // ignore
    }
  };

  if (onError) {
    es.onerror = onError;
  }

  return es;
}
