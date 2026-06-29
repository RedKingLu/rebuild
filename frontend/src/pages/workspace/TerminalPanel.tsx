/** R8 Controlled terminal — command input, execution via ExecutionProvider, stdout/stderr display. */
import { useState, useRef, useEffect } from 'react';
import { executeCommand, type ExecuteResult } from '../../services/workspaceService';

interface Props {
  projectId: string;
  /** R9-3A: callback to push execution results to outputHistory */
  onOutput?: (entry: { cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string }) => void;
}

const MAX_HISTORY = 50;

export function TerminalPanel({ projectId, onOutput }: Props) {
  const [command, setCommand] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [outputs, setOutputs] = useState<Array<{ cmd: string; result: ExecuteResult }>>([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [outputs]);

  const run = async () => {
    const cmd = command.trim();
    if (!cmd || busy) return;

    setCommand('');
    setHistoryIdx(-1);
    setHistory(prev => [cmd, ...prev].slice(0, MAX_HISTORY));
    setBusy(true);

    try {
      const result = await executeCommand(projectId, { command: cmd, language: 'shell' });
      setOutputs(prev => [...prev, { cmd, result }]);
      // R9-3A: Push to parent outputHistory
      onOutput?.({
        cmd, exit_code: result.exit_code,
        provider: result.provider, elapsed_ms: result.elapsed_ms,
        stdout: result.stdout, stderr: result.stderr,
      });
    } catch (e: any) {
      setOutputs(prev => [...prev, {
        cmd,
        result: { exit_code: -1, stdout: '', stderr: e.message || '请求失败', elapsed_ms: 0, provider: 'error', execution_mode: 'error', blocked: false },
      }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); run(); }
    else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = Math.min(historyIdx + 1, history.length - 1);
      setHistoryIdx(idx);
      if (history[idx]) setCommand(history[idx]);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const idx = Math.max(historyIdx - 1, -1);
      setHistoryIdx(idx);
      setCommand(idx === -1 ? '' : history[idx] || '');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 12 }}>
      {/* Output area */}
      <div ref={outputRef} style={{ flex: 1, overflow: 'auto', fontFamily: 'var(--mono)', padding: '4px 0' }}>
        {outputs.length === 0 && (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>
            受控终端 — 输入命令后通过 ExecutionProvider 执行。高风险命令将被拦截。
          </div>
        )}
        {outputs.map((o, i) => (
          <div key={i} style={{ marginBottom: 4 }}>
            <div style={{ color: 'var(--color-primary)', fontWeight: 600 }}>$ {o.cmd}</div>
            {o.result.stdout && (
              <pre style={{ margin: '2px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{o.result.stdout.trimEnd()}</pre>
            )}
            {o.result.stderr && (
              <pre style={{ margin: '2px 0', color: 'var(--red)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{o.result.stderr.trimEnd()}</pre>
            )}
            {o.result.blocked && (
              <span style={{ color: 'var(--amber)', fontWeight: 600 }}>⛔ 命令被安全策略拦截</span>
            )}
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
              exit={o.result.exit_code} · {o.result.provider} · {o.result.elapsed_ms}ms
              {o.result.session_id && <> · session={o.result.session_id}</>}
            </div>
          </div>
        ))}
        {busy && <div style={{ color: 'var(--color-text-muted)' }}>执行中…</div>}
      </div>

      {/* Input */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, borderTop: '1px solid var(--color-border)', paddingTop: 4, flexShrink: 0 }}>
        <span style={{ color: 'var(--color-primary)', fontWeight: 600, flexShrink: 0 }}>$</span>
        <input
          ref={inputRef}
          style={{
            flex: 1, border: 'none', background: 'transparent', fontFamily: 'var(--mono)', fontSize: 12,
            color: 'var(--color-text)', outline: 'none',
          }}
          value={command}
          onChange={e => setCommand(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={busy ? '执行中…' : '输入命令 (Enter 执行, ↑↓ 历史)'}
          disabled={busy}
        />
      </div>
    </div>
  );
}
