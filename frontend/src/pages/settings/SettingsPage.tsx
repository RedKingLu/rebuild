import { useState, useEffect, useCallback } from 'react';
import { useSettingsStore } from '../../stores';
import { Icon } from '../../components/ui/Icon';
import {
  listCredentials, createCredential, deleteCredential, rotateCredential,
  type CredentialInfo,
} from '../../services/credentialService';

const FONT_SCALES: [number, string][] = [[0.9, '小'], [1.0, '标准'], [1.1, '大'], [1.25, '特大']];

export function SettingsPage() {
  const theme = useSettingsStore(s => s.theme);
  const setTheme = useSettingsStore(s => s.setTheme);
  const fontScale = useSettingsStore(s => s.fontScale);
  const setFontScale = useSettingsStore(s => s.setFontScale);

  // ── 凭据管理状态 ──
  const [creds, setCreds] = useState<CredentialInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: '', plaintext_key: '', provider_ref: '' });
  const [busy, setBusy] = useState(false);
  const [rotateId, setRotateId] = useState('');
  const [rotateKey, setRotateKey] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { setCreds(await listCredentials()); }
    catch (e: any) { setError(e?.message || '加载凭据失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!form.name.trim() || !form.plaintext_key.trim()) { setError('名称与密钥均为必填'); return; }
    setBusy(true); setError('');
    try {
      await createCredential({
        name: form.name.trim(),
        plaintext_key: form.plaintext_key,
        provider_ref: form.provider_ref.trim() || undefined,
      });
      setForm({ name: '', plaintext_key: '', provider_ref: '' });
      setShowCreate(false);
      await load();
    } catch (e: any) { setError(e?.message || '创建失败'); }
    finally { setBusy(false); }
  };

  const handleDelete = async (id: string) => {
    setBusy(true); setError('');
    try { await deleteCredential(id); await load(); }
    catch (e: any) { setError(e?.message || '删除失败'); }
    finally { setBusy(false); }
  };

  const handleRotate = async (id: string) => {
    if (!rotateKey.trim()) { setError('请输入新密钥'); return; }
    setBusy(true); setError('');
    try { await rotateCredential(id, rotateKey); setRotateId(''); setRotateKey(''); await load(); }
    catch (e: any) { setError(e?.message || '轮换失败'); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <h1>设置</h1>
      <p className="sub">平台配置、安全、偏好。</p>

      {/* ── 外观 ── */}
      <div className="card" style={{ marginBottom: 14 }}>
        <b>外观</b>
        <div className="row" style={{ marginTop: 8, alignItems: 'center' }}>
          <span className="sub" style={{ fontSize: 13, width: 64 }}>主题</span>
          <button className={`btn sm ${theme === 'light' ? '' : 'ghost'}`} onClick={() => setTheme('light')}>浅色</button>
          <button className={`btn sm ${theme === 'dark' ? '' : 'ghost'}`} onClick={() => setTheme('dark')}>深色</button>
        </div>
        <div className="row" style={{ marginTop: 10, alignItems: 'center' }}>
          <span className="sub" style={{ fontSize: 13, width: 64 }}>界面字号</span>
          {FONT_SCALES.map(([v, label]) => (
            <button key={v} className={`btn sm ${Math.abs(fontScale - v) < 0.01 ? '' : 'ghost'}`} onClick={() => setFontScale(v)}>{label}</button>
          ))}
          <span className="sub" style={{ fontSize: 12, marginLeft: 8 }}>当前 {Math.round(fontScale * 100)}%</span>
        </div>
      </div>

      {/* ── 凭据管理 ── */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="spread">
          <b><Icon name="key" /> 凭据管理</b>
          <button className="btn sm" onClick={() => { setShowCreate(s => !s); setError(''); }}>
            <Icon name="add" /> 新增凭据
          </button>
        </div>
        <p className="sub" style={{ fontSize: 12, marginTop: 4 }}>
          统一管理 BYOK 凭据（API Key / Token）。所有凭据均经 AES-256-GCM 加密存储，
          列表仅展示掩码与指纹，API 不返回明文。模型 Key 也可在<a href="/models">模型页</a>就近录入。
        </p>

        {error && <div className="hash" style={{ color: 'var(--red)', fontSize: 12, marginTop: 6 }}><Icon name="error" /> {error}</div>}

        {showCreate && (
          <div className="card" style={{ marginTop: 10 }}>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ width: 200 }}>
                <label>名称</label>
                <input placeholder="如 deepseek-prod" value={form.name}
                       onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div style={{ width: 180 }}>
                <label>所属（可选）</label>
                <input placeholder="provider_ref" value={form.provider_ref}
                       onChange={e => setForm(f => ({ ...f, provider_ref: e.target.value }))} />
              </div>
              <div style={{ width: 240 }}>
                <label>密钥明文（仅提交，不回显）</label>
                <input type="password" placeholder="sk-…" value={form.plaintext_key}
                       onChange={e => setForm(f => ({ ...f, plaintext_key: e.target.value }))} />
              </div>
              <button className="btn sm" disabled={busy} onClick={handleCreate}>保存</button>
              <button className="btn sm ghost" disabled={busy} onClick={() => setShowCreate(false)}>取消</button>
            </div>
          </div>
        )}

        <div style={{ marginTop: 10 }}>
          {loading ? <div className="empty">加载中…</div>
            : creds.length === 0 ? <div className="empty">暂无凭据。点击「新增凭据」添加。</div>
            : creds.map(c => (
                <div key={c.credential_id} className="listrow">
                  <div>
                    <div className="ttl">
                      {c.name} <span className={`tag ${c.status === 'active' ? 'green' : 'grey'}`}>{c.status}</span>
                    </div>
                    <div className="meta">
                      掩码 <code>{c.masked_key}</code> · 指纹 <code>{c.key_fingerprint}</code> · 来源 {c.provider_ref || c.key_source || '—'}
                    </div>
                    {rotateId === c.credential_id && (
                      <div className="row" style={{ gap: 6, marginTop: 6, alignItems: 'center' }}>
                        <input type="password" placeholder="新密钥明文" value={rotateKey}
                               onChange={e => setRotateKey(e.target.value)} style={{ width: 220 }} />
                        <button className="btn sm" disabled={busy} onClick={() => handleRotate(c.credential_id)}>确认轮换</button>
                      </div>
                    )}
                  </div>
                  <div style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn sm ghost" disabled={busy} title="轮换密钥"
                            onClick={() => { setRotateId(rotateId === c.credential_id ? '' : c.credential_id); setRotateKey(''); }}>
                      <Icon name="refresh" /> 轮换
                    </button>
                    <button className="btn sm danger" disabled={busy} title="删除"
                            onClick={() => handleDelete(c.credential_id)}>
                      <Icon name="delete" /> 删除
                    </button>
                  </div>
                </div>
              ))}
        </div>
        <div className="hash" style={{ fontSize: 12, marginTop: 8 }}>
          · Git 平台 → <a href="/integrations">集成页 · 代码托管</a>（OAuth 授权）
          · 远程主机 → <a href="/integrations">集成页 · 远程资源</a>
          · 飞书 Webhook → <a href="/integrations">集成页 · 其他集成</a>（加密存储，API 仅返回掩码）
        </div>
      </div>

      {/* ── 执行安全 ── */}
      <div className="card" style={{ marginBottom: 14 }}>
        <b>执行安全 · 自动模式分级（AUTO-L0~L5）</b>
        <div style={{ marginTop: 8, fontSize: 13 }}>
          {['L0 只读自动', 'L1 生成产物不写工作区', 'L2 写临时目录', 'L3 写工作区需确认/预授权', 'L4 shell·测试需白名单+审计', 'L5 网络·删除·大改默认禁止'].map(l => (
            <div key={l} className="hash" style={{ padding: '3px 0' }}>{l}</div>
          ))}
        </div>
        <span className="tag violet" style={{ marginTop: 8 }}>Mock</span>
      </div>

      <div className="cardgrid">
        <div className="card"><div className="spread"><b>Hook：写盘前 Policy 检查</b><span className="tag green">Mock 启用</span></div></div>
        <div className="card"><div className="spread"><b>记忆策略</b><span className="tag grey">默认关</span></div></div>
        <div className="card"><div className="spread"><b>安全扫描策略</b><span className="tag amber">占位</span></div></div>
        <div className="card"><div className="spread"><b>外部 Agent 自我批准</b><span className="tag red">禁止</span></div></div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <b>关于</b>
        <div className="hash" style={{ marginTop: 6 }}>平台：rebuild · 版本：V26.1.1 · 当前阶段：R7 概览/项目/集成真实化</div>
        <span className="tag violet" style={{ marginTop: 4 }}>凭证据·可审计</span>
      </div>
    </div>
  );
}
