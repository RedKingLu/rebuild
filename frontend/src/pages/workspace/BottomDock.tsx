/** Bottom Dock — Terminal / Output / Problems / Task Progress tabs. */
import { useState } from 'react';
import { TerminalPanel } from './TerminalPanel';

type DockTab = 'terminal' | 'output' | 'problems' | 'progress';

interface Props {
  projectId: string;
  outputHistory: Array<{ cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string }>;
  setOutputHistory: (updater: (prev: Array<{ cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string }>) => Array<{ cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string }>) => void;
  sessions: Array<Record<string, unknown>>;
}

export function BottomDock({ projectId, outputHistory, setOutputHistory, sessions }: Props) {
  const [tab, setTab] = useState<DockTab>('terminal');

  const tabs: [DockTab, string][] = [
    ['terminal', '终端'],
    ['output', '输出'],
    ['problems', '问题'],
    ['progress', '任务进度'],
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
        {tabs.map(([k, label]) => (
          <button
            key={k}
            className={`tab${tab === k ? ' active' : ''}`}
            style={{ padding: '4px 12px', fontSize: 12, border: 'none', background: tab === k ? 'var(--color-primary-soft)' : 'transparent', cursor: 'pointer', color: tab === k ? 'var(--color-primary)' : 'var(--color-text-muted)', borderRadius: '4px 4px 0 0' }}
            onClick={() => setTab(k)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {tab === 'terminal' && <TerminalPanel projectId={projectId} onOutput={(entry) => setOutputHistory(prev => [...prev, entry])} />}
        {tab === 'output' && (
          <div style={{ fontSize: 12 }}>
            <b style={{ fontSize: 13 }}>执行输出</b>
            {outputHistory.length === 0 ? (
              <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>暂无执行记录。在终端面板执行命令后可在此查看格式化输出。</div>
            ) : (
              outputHistory.map((o, i) => (
                <div key={i} style={{ marginTop: 8, padding: 6, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
                  <div style={{ fontWeight: 600 }}>$ {o.cmd}</div>
                  <div>exit={o.exit_code} · provider={o.provider} · {o.elapsed_ms}ms</div>
                  {o.stdout && <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap' }}>{o.stdout.slice(0, 500)}</pre>}
                  {o.stderr && <pre style={{ margin: '4px 0', color: 'var(--red)', whiteSpace: 'pre-wrap' }}>{o.stderr.slice(0, 500)}</pre>}
                </div>
              ))
            )}
          </div>
        )}
        {tab === 'problems' && (
          <div style={{ fontSize: 12 }}>
            <b style={{ fontSize: 13 }}>问题</b>
            <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>
              执行迁移后将在此展示编译错误、测试失败、代码问题等。
            </div>
          </div>
        )}
        {tab === 'progress' && (
          <div style={{ fontSize: 12 }}>
            <b style={{ fontSize: 13 }}>任务进度</b>
            {sessions.length === 0 ? (
              <div style={{ color: 'var(--color-text-muted)', marginTop: 8, fontSize: 11 }}>暂无执行会话。在终端执行命令后将在此展示进度。</div>
            ) : (
              sessions.map((s, i) => (
                <div key={i} style={{ marginTop: 6, padding: 6, background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ fontWeight: 600 }}>{s.session_id as string}</span>
                    <span className={`tag ${s.status === 'succeeded' ? 'green' : s.status === 'failed' ? 'red' : ''}`}>{s.status as string}</span>
                  </div>
                  <div style={{ color: 'var(--color-text-muted)' }}>{s.command as string} · {s.provider as string} · exit={s.exit_code as number}</div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
