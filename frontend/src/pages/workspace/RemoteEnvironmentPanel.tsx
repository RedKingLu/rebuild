/** RemoteEnvironmentPanel — R14-4 (WP6).
 *
 * Workspace "remote" activity: bind/unbind remote hosts, set the default
 * execution environment, inspect remote invocation history, and run a read-only
 * environment detection (G8).  D-074: every action calls a real API and renders
 * the real response.
 */

import { useState, useEffect, useCallback } from 'react';
import { Icon } from '../../components/ui/Icon';
import {
  listRemoteHosts, type RemoteHostInfo,
} from '../../services/integrationService';
import {
  fetchEnvBlock, addBinding, setDefaultBinding, removeBinding,
  fetchInvocations, detectEnvironment,
  type EnvironmentBlock, type RemoteInvocationItem,
} from '../../services/workspaceService';

export function RemoteEnvironmentPanel({ projectId }: { projectId: string }) {
  const [hosts, setHosts] = useState<RemoteHostInfo[]>([]);
  const [block, setBlock] = useState<EnvironmentBlock | null>(null);
  const [invocations, setInvocations] = useState<RemoteInvocationItem[]>([]);
  const [detecting, setDetecting] = useState<string | null>(null);
  const [detectResult, setDetectResult] = useState<Record<string, any> | null>(null);
  const [msg, setMsg] = useState<string>('');

  const reload = useCallback(async () => {
    try {
      const [h, b, i] = await Promise.all([
        listRemoteHosts().catch(() => []),
        fetchEnvBlock(projectId).catch(() => null),
        fetchInvocations(projectId).catch(() => []),
      ]);
      setHosts(h); setBlock(b); setInvocations(i);
    } catch (e: any) {
      setMsg(e.message);
    }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  const bind = async (hostId: string, asDefault: boolean) => {
    try {
      const env = await addBinding(projectId, hostId, asDefault);
      setBlock(env); setMsg(asDefault ? '已绑定并设为默认环境' : '已绑定');
      await reload();
    } catch (e: any) { setMsg(e.message); }
  };

  const unbind = async (bindingId: string) => {
    try { await removeBinding(projectId, bindingId); await reload(); setMsg('已移除绑定'); }
    catch (e: any) { setMsg(e.message); }
  };

  const setDefault = async (bindingId: string | null) => {
    try { const env = await setDefaultBinding(projectId, bindingId); setBlock(env); setMsg('默认环境已更新'); }
    catch (e: any) { setMsg(e.message); }
  };

  const detect = async (hostId: string) => {
    setDetecting(hostId); setDetectResult(null);
    try { const r = await detectEnvironment(projectId, hostId); setDetectResult(r); }
    catch (e: any) { setMsg(e.message); }
    finally { setDetecting(null); }
  };

  const bindingHostIds = block?.bindings || [];

  return (
    <div style={{ padding: 8, fontSize: 12 }}>
      <b>远程环境绑定</b>
      <div className="sub" style={{ margin: '4px 0 8px' }}>将远程主机绑定到工作区，并选择默认执行环境。</div>

      {/* Bind existing hosts */}
      <div style={{ marginBottom: 12 }}>
        <div className="tag" style={{ marginBottom: 4 }}>可绑定的远程主机</div>
        {hosts.length === 0 && <div className="sub">暂无远程主机，请先在「集成」页添加。</div>}
        {hosts.map(h => {
          const bound = bindingHostIds.includes(h.remote_host_id);
          const isDefault = block?.default_binding_id === h.remote_host_id;
          return (
            <div key={h.remote_host_id} className="card" style={{ padding: 8, marginBottom: 6 }}>
              <div className="spread">
                <div><b>{h.name}</b> <span className="tag">{h.host_type}</span></div>
                <div className="row" style={{ gap: 4 }}>
                  {!bound && <button className="btn sm" onClick={() => bind(h.remote_host_id, false)}>绑定</button>}
                  {bound && !isDefault && <button className="btn sm" onClick={() => setDefault(h.remote_host_id)}>设为默认</button>}
                  {isDefault && <span className="tag" style={{ background: 'var(--green)', color: '#fff' }}>默认</span>}
                  <button className="btn sm ghost" onClick={() => detect(h.remote_host_id)}>{detecting === h.remote_host_id ? '…' : '探测'}</button>
                </div>
              </div>
              <div className="sub" style={{ marginTop: 2 }}>{h.address}:{h.port} {h.os_name ? `· ${h.os_name}` : ''}</div>
              {bound && <button className="btn sm ghost" style={{ marginTop: 4, color: 'var(--red)' }} onClick={() => unbind(h.remote_host_id)}>解绑</button>}
            </div>
          );
        })}
      </div>

      {/* Detection result */}
      {detectResult && (
        <div className="card" style={{ padding: 8, marginBottom: 12 }}>
          <b>环境探测 {detectResult.ok ? '✅' : '❌'}</b>
          {detectResult.ok ? (
            <div style={{ marginTop: 4 }}>
              <div>OS: {detectResult.os || '未知'}</div>
              <div>Python: {detectResult.python || '未检测到'}</div>
              <div>内核: {detectResult.kernel}</div>
              {detectResult.services?.length > 0 && (
                <div>服务: {detectResult.services.map((s: any) => `${s.name}:${s.port}`).join(', ')}</div>
              )}
            </div>
          ) : (
            <div className="sub" style={{ marginTop: 4 }}>{detectResult.error || '探测失败'}</div>
          )}
        </div>
      )}

      {/* Invocation history */}
      <div>
        <div className="tag" style={{ marginBottom: 4 }}>调用历史 ({invocations.length})</div>
        {invocations.length === 0 && <div className="sub">暂无远程调用记录。</div>}
        {invocations.map(i => (
          <div key={i.invocation_id} className="card" style={{ padding: 6, marginBottom: 4 }}>
            <div className="spread">
              <span className="tag">{i.provider}</span>
              <span className={i.status === 'success' ? 'sub' : ''} style={{ color: i.status === 'success' ? 'var(--green)' : 'var(--red)' }}>
                {i.status} (exit={i.exit_code})
              </span>
            </div>
            <div className="sub">摘要 {i.command_digest} · {i.elapsed_ms}ms · {i.risk_level}</div>
          </div>
        ))}
      </div>

      {msg && <div className="banner info" style={{ fontSize: 11, marginTop: 8 }}>{msg}</div>}
    </div>
  );
}
