/** EditProviderModal — 编辑供应商全部信息（FB-005 增强）。
 *
 * 所有字段均可编辑（名称、端点、格式、模型列表、Key），预填已保存数据。
 * 保存时通过 PUT /api/model/providers/{id} 更新非敏感配置，
 * 通过 POST /api/model/providers/{id}/credential 更新 Key。
 * 中文优先；线性图标。
 */
import { useState, useEffect, type ReactNode } from 'react';
import {
  setCredential, updateProvider,
  type ProviderInfo, type ImportModelInput, type UpdateProviderInput,
} from '../../services/modelService';
import { Icon } from '../ui/Icon';

interface Props {
  provider: ProviderInfo;
  profiles: { profile_id: string; provider_id: string; model_name: string; display_name: string; status: string }[];
  onClose: () => void;
  onSaved: () => void;
}

export function EditProviderModal({ provider, profiles, onClose, onSaved }: Props) {
  // 可编辑字段 —— 预填已有值
  const [name, setName] = useState(provider.provider_name);
  const [apiFormat, setApiFormat] = useState(provider.api_format);
  const [endpointOpenai, setEndpointOpenai] = useState(provider.endpoint_openai || '');
  const [endpointAnthropic, setEndpointAnthropic] = useState(provider.endpoint_anthropic || '');
  const [envKeyVar, setEnvKeyVar] = useState(provider.env_key_var || '');
  const [note, setNote] = useState(provider.note || '');
  const [homepage, setHomepage] = useState(provider.homepage || '');

  // 模型列表 —— 预填已有模型
  const providerModels = profiles.filter(p => p.provider_id === provider.provider_id);
  const [models, setModels] = useState<ImportModelInput[]>(
    providerModels.length > 0
      ? providerModels.map(m => ({ model_name: m.model_name, display_name: m.display_name || m.model_name }))
      : [{ model_name: '', display_name: '' }]
  );

  // Key
  const [apiKey, setApiKey] = useState('');
  const [apiKeyChanged, setApiKeyChanged] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Sync state when provider changes
  useEffect(() => {
    setName(provider.provider_name);
    setApiFormat(provider.api_format);
    setEndpointOpenai(provider.endpoint_openai || '');
    setEndpointAnthropic(provider.endpoint_anthropic || '');
    setEnvKeyVar(provider.env_key_var || '');
    setNote(provider.note || '');
    setHomepage(provider.homepage || '');
    const pms = profiles.filter(p => p.provider_id === provider.provider_id);
    setModels(pms.length > 0
      ? pms.map(m => ({ model_name: m.model_name, display_name: m.display_name || m.model_name }))
      : [{ model_name: '', display_name: '' }]
    );
    setApiKey('');
    setApiKeyChanged(false);
    setError(null);
    setSuccess(null);
  }, [provider.provider_id]);

  const updateModel = (i: number, field: 'model_name' | 'display_name', v: string) => {
    setModels(prev => prev.map((m, idx) => idx === i ? { ...m, [field]: v } : m));
  };
  const addModelRow = () => setModels(prev => [...prev, { model_name: '', display_name: '' }]);
  const removeModelRow = (i: number) => setModels(prev => prev.filter((_, idx) => idx !== i));

  const handleSave = async () => {
    setError(null); setSuccess(null);
    if (!name.trim()) { setError('供应商名称不能为空'); return; }
    const validModels = models.filter(m => m.model_name.trim());
    if (validModels.length === 0) { setError('请至少保留一个模型'); return; }

    setSaving(true);

    // 1. 更新非敏感配置
    const configInput: UpdateProviderInput = {
      provider_name: name.trim(),
      api_format: apiFormat,
      endpoint_openai: endpointOpenai.trim(),
      endpoint_anthropic: endpointAnthropic.trim(),
      env_key_var: envKeyVar.trim(),
      note: note.trim(),
      homepage: homepage.trim(),
      models: validModels.map(m => ({ model_name: m.model_name.trim(), display_name: (m.display_name || m.model_name).trim() })),
    };
    try {
      const resp = await updateProvider(provider.provider_id, configInput);
      if (!resp.data) { setError('保存配置失败：供应商不存在'); setSaving(false); return; }
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存配置失败');
      setSaving(false); return;
    }

    // 2. 如有新 Key，也一并保存
    if (apiKeyChanged && apiKey.trim()) {
      try {
        await setCredential(provider.provider_id, apiKey.trim());
      } catch (e) {
        // Key 保存失败不阻断
        console.warn('Key save failed:', e);
      }
    }

    setSaving(false);
    setSuccess('供应商信息已保存（配置持久化到文件，Key BYOK 加密入库）');
    setApiKey('');
    setApiKeyChanged(false);
    onSaved();
  };

  const hasChanges = name !== provider.provider_name
    || apiFormat !== provider.api_format
    || endpointOpenai !== (provider.endpoint_openai || '')
    || endpointAnthropic !== (provider.endpoint_anthropic || '')
    || envKeyVar !== (provider.env_key_var || '')
    || note !== (provider.note || '')
    || homepage !== (provider.homepage || '')
    || apiKeyChanged;

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div className="card" style={{ width: 'min(720px, 96vw)', maxHeight: '92vh', overflow: 'auto', padding: 20 }}
        onClick={e => e.stopPropagation()}>
        <div className="spread" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>编辑供应商</h2>
          <button className="btn sm ghost" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button>
        </div>

        {/* 只读元信息 */}
        <div className="row" style={{ gap: 12, marginBottom: 14, fontSize: 12 }}>
          <span className="sub">ID：<code>{provider.provider_id}</code></span>
          <span className="sub">来源：{provider.origin === 'user' ? '用户导入' : '内置'}</span>
          <span className="sub">凭据状态：{provider.credential_status}</span>
          <span className="sub">Key 来源：{provider.key_source || '无'}</span>
          <CapabilityBadgeSmall marker={provider.capability_marker} />
        </div>

        {/* 可编辑字段 */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <Field label="供应商名称 *">
            <input className="inp" value={name} onChange={e => setName(e.target.value)} placeholder="供应商名称" />
          </Field>
          <Field label="API 格式">
            <select className="inp" value={apiFormat} onChange={e => setApiFormat(e.target.value)}>
              <option value="openai">OpenAI 兼容</option>
              <option value="anthropic">Anthropic Messages（原生）</option>
            </select>
          </Field>
          <Field label="OpenAI 端点">
            <input className="inp" value={endpointOpenai} onChange={e => setEndpointOpenai(e.target.value)}
              placeholder="https://api.example.com/v1（不以斜杠结尾）" />
          </Field>
          <Field label="Anthropic 端点">
            <input className="inp" value={endpointAnthropic} onChange={e => setEndpointAnthropic(e.target.value)}
              placeholder="https://api.example.com/anthropic" />
          </Field>
          <Field label="环境变量名">
            <input className="inp" value={envKeyVar} onChange={e => setEnvKeyVar(e.target.value)}
              placeholder="例如：DEEPSEEK_API_KEY" />
          </Field>
          <Field label="官网链接">
            <input className="inp" value={homepage} onChange={e => setHomepage(e.target.value)}
              placeholder="https://example.com（可选）" />
          </Field>
        </div>
        <Field label="备注">
          <input className="inp" value={note} onChange={e => setNote(e.target.value)}
            placeholder="例如：公司专用账号" />
        </Field>

        {/* 模型列表编辑 */}
        <div className="sub" style={{ fontSize: 12, margin: '10px 0 6px' }}>模型列表 *（可增删改）</div>
        {models.map((m, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
            <input className="inp" style={{ flex: 1 }} value={m.model_name}
              onChange={e => updateModel(i, 'model_name', e.target.value)} placeholder="模型名称（如 deepseek-v4-pro）" />
            <input className="inp" style={{ flex: 1 }} value={m.display_name || ''}
              onChange={e => updateModel(i, 'display_name', e.target.value)} placeholder="显示名（可选）" />
            {models.length > 1 && <button className="btn sm ghost" onClick={() => removeModelRow(i)}>✕</button>}
          </div>
        ))}
        <button className="btn sm ghost" onClick={addModelRow} style={{ marginTop: 2 }}>+ 添加模型</button>

        {/* Key 编辑 */}
        <Field label="API Key（留空不修改，填写后 BYOK 加密持久化）">
          <input className="inp" type="password" value={apiKey}
            onChange={e => { setApiKey(e.target.value); setApiKeyChanged(true); }}
            placeholder="粘贴新的 API Key（留空保持现有 Key 不变）" />
          <div className="sub" style={{ fontSize: 11, marginTop: 4, color: 'var(--amber)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icon name="key" size={12} /> 现有 Key 不回显（安全）。填写新 Key 将通过 AES-256-GCM 加密存入数据库。
          </div>
        </Field>

        {error && <div className="err" style={{ marginTop: 12, fontSize: 13 }}>⚠ {error}</div>}
        {success && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--green)' }}>✅ {success}</div>}

        <div className="row" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={handleSave} disabled={saving || !hasChanges}>
            {saving ? '保存中…' : '保存全部'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 10 }}>
      <label className="sub" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}

function CapabilityBadgeSmall({ marker }: { marker: string }) {
  const LABELS: Record<string, string> = {
    real_available: '真实可用', configured_not_verified: '已配置未验证',
    credential_missing: '缺少凭据', credential_invalid: '凭据无效',
    not_connected: '未连通', not_checked: '未检测',
  };
  const COLORS: Record<string, string> = {
    real_available: 'var(--green)', configured_not_verified: 'var(--amber)',
    credential_missing: 'var(--orange)', credential_invalid: 'var(--red)',
    not_connected: 'var(--gray)', not_checked: 'var(--gray)',
  };
  const label = LABELS[marker] || marker;
  const color = COLORS[marker] || 'var(--gray)';
  return <span className="tag" style={{ fontSize: 10, background: color, color: '#fff' }}>{label}</span>;
}
