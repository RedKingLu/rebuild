/** PlatformAssistant — 全局浮动 AI 精灵（R5-4 真实化）。
 *
 * 用户已规划：浮动弹窗、可输入、可切换模型。本组件接入真实 ModelGateway：
 *  - 真实对话：POST /api/assistant/chat（经 ModelGateway，D-073）
 *  - 模型/供应商切换：下拉选择 Profile，作为 user_override 传入
 *  - 连通性自测：对所选模型的供应商发起 self-test（06 §3 模型接入体验入口）
 * 边界（D-073）：仅连通+对话答疑；不改默认策略、不产 Evidence、不执行 P0-P6、不代操作。
 * 中文优先（07 视觉系统与前端文案）；不泄露 Key（响应不含 Key）。
 */
import { useState, useEffect, useRef } from 'react';
import {
  listProviders, listProfiles, assistantChat, selfTest,
  type ProviderInfo, type ModelProfileInfo, type AssistantChatResult,
} from '../../services/modelService';
import { Icon } from '../ui/Icon';

interface Msg { role: 'user' | 'assistant' | 'system'; content: string; meta?: string; }

export function PlatformAssistant() {
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [profiles, setProfiles] = useState<ModelProfileInfo[]>([]);
  const [providerId, setProviderId] = useState<string>('');
  const [profileId, setProfileId] = useState<string>('');
  const [messages, setMessages] = useState<Msg[]>([
    { role: 'assistant', content: '你好！我是平台助手（AI 精灵）。可以问我 rebuild 平台的使用问题，或在下方先选服务商、再选模型，并做连通性自测。' },
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [testing, setTesting] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 每次打开都重新拉取（保证新导入的供应商/模型即时出现，非缓存）
  useEffect(() => {
    if (!open) return;
    Promise.all([listProviders(), listProfiles()]).then(([pr, pf]) => {
      const provs = (pr.data?.providers || []).filter(p => p.credential_status === 'configured');
      const profs = pf.data?.profiles || [];
      setProviders(provs);
      setProfiles(profs);
      // 默认选第一个已配置供应商及其第一个已配置模型
      setProviderId(prev => {
        const stillValid = provs.some(p => p.provider_id === prev);
        const nextProv = stillValid ? prev : (provs[0]?.provider_id || '');
        const provModels = profs.filter(m => m.provider_id === nextProv && m.status === 'configured');
        setProfileId(pid => {
          const ok = provModels.some(m => m.profile_id === pid);
          return ok ? pid : (provModels[0]?.profile_id || '');
        });
        return nextProv;
      });
    }).catch(() => {});
  }, [open]);

  // 切换服务商时，重置为该服务商第一个已配置模型
  const onProviderChange = (pid: string) => {
    setProviderId(pid);
    const provModels = profiles.filter(m => m.provider_id === pid && m.status === 'configured');
    setProfileId(provModels[0]?.profile_id || '');
  };

  const modelOptions = profiles.filter(m => m.provider_id === providerId);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setMessages(m => [...m, { role: 'user', content: text }]);
    setSending(true);
    try {
      const resp = await assistantChat(text, profileId || undefined);
      const d = resp.data as AssistantChatResult;
      if (d.status === 'completed') {
        setMessages(m => [...m, { role: 'assistant', content: d.reply || '(空响应)', meta: `${d.model} · ${d.latency_ms}ms · 来源 ${d.source}` }]);
      } else {
        setMessages(m => [...m, { role: 'system', content: `调用未完成：${d.status}${d.error_message ? ' · ' + d.error_message : ''}` }]);
      }
    } catch (e) {
      setMessages(m => [...m, { role: 'system', content: `请求失败：${e instanceof Error ? e.message : String(e)}` }]);
    } finally {
      setSending(false);
    }
  };

  const runSelfTest = async () => {
    const profile = profiles.find(p => p.profile_id === profileId);
    if (!profile) { setMessages(m => [...m, { role: 'system', content: '请先选择服务商与模型。' }]); return; }
    setTesting(true);
    setMessages(m => [...m, { role: 'system', content: `正在对 ${profile.display_name}（${profile.provider_id}）做连通性自测…` }]);
    try {
      const resp = await selfTest(profile.provider_id, profile.profile_id);
      const d = resp.data;
      setMessages(m => [...m, {
        role: 'system',
        content: d.status === 'reachable'
          ? `✅ 连通成功：${profile.display_name} · ${d.latency_ms}ms`
          : `❌ 自测失败：${d.status}${d.error_message ? ' · ' + d.error_message : ''}`,
      }]);
    } catch (e) {
      setMessages(m => [...m, { role: 'system', content: `自测失败：${e instanceof Error ? e.message : String(e)}` }]);
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        style={{
          position: 'fixed', bottom: 24, right: 24, width: 52, height: 52, borderRadius: '50%',
          background: 'var(--accent-ink)', color: '#fff', border: 'none',
          boxShadow: '0 4px 16px rgba(0,0,0,.15)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
        }}
        title="平台助手"
        aria-label="平台助手"
      >
        <Icon name={open ? 'close' : 'robot'} size={26} />
      </button>
      {open && (
        <div style={{
          position: 'fixed', bottom: 84, right: 24, width: 400, height: 560, background: 'var(--surface)',
          border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 8px 32px rgba(0,0,0,.12)',
          zIndex: 1000, display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          {/* 头部 */}
          <div className="spread" style={{ padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
            <div>
              <b style={{ fontSize: 14 }}>平台助手</b>
              <div className="sub" style={{ fontSize: 11 }}>经 ModelGateway 真实调用 · 仅答疑与连通自测</div>
            </div>
            <button className="btn sm ghost" onClick={() => setOpen(false)} aria-label="关闭"><Icon name="close" size={16} /></button>
          </div>

          {/* 消息区 */}
          <div ref={bodyRef} style={{ flex: 1, padding: 16, overflow: 'auto' }}>
            {messages.map((m, i) => (
              <div key={i} style={{
                display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10,
              }}>
                <div style={{
                  maxWidth: '85%', padding: '8px 12px', borderRadius: 10, fontSize: 13, whiteSpace: 'pre-wrap',
                  background: m.role === 'user' ? 'var(--accent-ink)' : m.role === 'system' ? 'var(--surface-2)' : 'var(--surface-2)',
                  color: m.role === 'user' ? '#fff' : 'var(--fg)',
                  border: m.role === 'system' ? '1px dashed var(--line)' : 'none',
                }}>
                  {m.content}
                  {m.meta && <div style={{ fontSize: 10, opacity: .7, marginTop: 4 }}>{m.meta}</div>}
                </div>
              </div>
            ))}
            {sending && <div className="sub" style={{ fontSize: 12 }}>助手思考中…</div>}
          </div>

          {/* 先选服务商，再选模型 + 自测 */}
          <div style={{ padding: '8px 16px', borderTop: '1px solid var(--line)', display: 'flex', gap: 6, alignItems: 'center' }}>
            <select className="inp" style={{ flex: 1, fontSize: 12 }} value={providerId} onChange={e => onProviderChange(e.target.value)} title="服务商">
              {providers.length === 0 && <option value="">（无已配置服务商）</option>}
              {providers.map(p => (
                <option key={p.provider_id} value={p.provider_id}>{p.provider_name}</option>
              ))}
            </select>
            <select className="inp" style={{ flex: 1, fontSize: 12 }} value={profileId} onChange={e => setProfileId(e.target.value)} title="模型">
              {modelOptions.length === 0 && <option value="">（无模型）</option>}
              {modelOptions.map(m => (
                <option key={m.profile_id} value={m.profile_id} disabled={m.status !== 'configured'}>
                  {m.display_name}{m.status !== 'configured' ? '（未配置）' : ''}
                </option>
              ))}
            </select>
            <button className="btn sm ghost" onClick={runSelfTest} disabled={testing || !profileId} title="连通性自测">
              {/* R19-3-04：同一图标位不再半 emoji 半 Icon */}
              <Icon name={testing ? 'refresh' : 'plug'} size={15} />
            </button>
          </div>

          {/* 输入区 */}
          <div style={{ padding: '10px 16px 14px', borderTop: '1px solid var(--line)', display: 'flex', gap: 8 }}>
            <input className="inp" style={{ flex: 1, fontSize: 13 }} value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') send(); }}
              placeholder="输入消息，回车发送…" disabled={sending} />
            <button className="btn sm" onClick={send} disabled={sending || !input.trim()} title="发送" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Icon name="send" size={14} />发送
            </button>
          </div>
        </div>
      )}
    </>
  );
}
