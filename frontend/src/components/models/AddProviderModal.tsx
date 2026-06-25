/** AddProviderModal — 导入用户自定义供应商（R5-4）。
 *
 * 参照 产物/草稿 截图 3「添加新供应商」。非敏感配置落盘到 user_providers.yaml，
 * API Key 仅注入后端进程内存（volatile，永不落盘，AGENTS.md §12.1）。
 * 中文优先（文档/06-UX与前端/07 视觉系统与前端文案）。
 */
import { useState, type ReactNode } from 'react';
import { createProvider, type CreateProviderInput } from '../../services/modelService';
import { Icon } from '../ui/Icon';

interface Preset {
  key: string;
  label: string;
  api_format: 'openai' | 'anthropic';
  endpoint: string;
  env_key_var: string;
}

// 常见供应商预置（仅非敏感默认值；Key 仍需用户填写）
const PRESETS: Preset[] = [
  { key: 'deepseek', label: 'DeepSeek 官方', api_format: 'openai', endpoint: 'https://api.deepseek.com', env_key_var: 'DEEPSEEK_API_KEY' },
  { key: 'openai', label: 'OpenAI', api_format: 'openai', endpoint: 'https://api.openai.com/v1', env_key_var: 'OPENAI_API_KEY' },
  { key: 'anthropic', label: 'Anthropic', api_format: 'anthropic', endpoint: 'https://api.anthropic.com', env_key_var: 'ANTHROPIC_API_KEY' },
  { key: 'openrouter', label: 'OpenRouter', api_format: 'openai', endpoint: 'https://openrouter.ai/api/v1', env_key_var: 'OPENROUTER_API_KEY' },
  { key: 'custom', label: '自定义', api_format: 'openai', endpoint: '', env_key_var: '' },
];

interface Props {
  onClose: () => void;
  onCreated: () => void;
}

export function AddProviderModal({ onClose, onCreated }: Props) {
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [homepage, setHomepage] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [apiFormat, setApiFormat] = useState<'openai' | 'anthropic'>('openai');
  const [envKeyVar, setEnvKeyVar] = useState('');
  const [models, setModels] = useState<{ model_name: string; display_name: string }[]>([
    { model_name: '', display_name: '' },
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activePreset, setActivePreset] = useState<string>('');

  const applyPreset = (p: Preset) => {
    setActivePreset(p.key);
    setApiFormat(p.api_format);
    setEndpoint(p.endpoint);
    setEnvKeyVar(p.env_key_var);
    if (p.key !== 'custom' && !name) setName(p.label);
  };

  const updateModel = (i: number, field: 'model_name' | 'display_name', v: string) => {
    setModels(prev => prev.map((m, idx) => idx === i ? { ...m, [field]: v } : m));
  };
  const addModelRow = () => setModels(prev => [...prev, { model_name: '', display_name: '' }]);
  const removeModelRow = (i: number) => setModels(prev => prev.filter((_, idx) => idx !== i));

  const handleSubmit = async () => {
    setError(null);
    if (!name.trim()) { setError('请填写供应商名称'); return; }
    const validModels = models.filter(m => m.model_name.trim());
    if (validModels.length === 0) { setError('请至少添加一个模型名称'); return; }

    const input: CreateProviderInput = {
      provider_name: name.trim(),
      api_format: apiFormat,
      endpoint_openai: apiFormat === 'openai' ? endpoint.trim() : '',
      endpoint_anthropic: apiFormat === 'anthropic' ? endpoint.trim() : '',
      env_key_var: envKeyVar.trim(),
      note: note.trim(),
      homepage: homepage.trim(),
      api_key: apiKey.trim() || undefined,
      models: validModels.map(m => ({ model_name: m.model_name.trim(), display_name: m.display_name.trim() || m.model_name.trim() })),
    };
    setSubmitting(true);
    try {
      const resp = await createProvider(input);
      if (!resp.data) { setError('创建失败：可能供应商已存在'); return; }
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div className="card" style={{ width: 'min(720px, 96vw)', maxHeight: '92vh', overflow: 'auto', padding: 20 }}
        onClick={e => e.stopPropagation()}>
        <div className="spread" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>添加新供应商</h2>
          <button className="btn sm ghost" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button>
        </div>

        {/* 预置 */}
        <div className="sub" style={{ fontSize: 12, marginBottom: 6 }}>常见供应商（点击套用默认配置，Key 仍需填写）</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
          {PRESETS.map(p => (
            <button key={p.key} className={`btn sm ${activePreset === p.key ? '' : 'ghost'}`}
              onClick={() => applyPreset(p)}>{p.label}</button>
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <Field label="供应商名称 *">
            <input className="inp" value={name} onChange={e => setName(e.target.value)} placeholder="例如：Claude 官方" />
          </Field>
          <Field label="备注">
            <input className="inp" value={note} onChange={e => setNote(e.target.value)} placeholder="例如：公司专用账号" />
          </Field>
          <Field label="官网链接">
            <input className="inp" value={homepage} onChange={e => setHomepage(e.target.value)} placeholder="https://example.com（可选）" />
          </Field>
          <Field label="API 格式">
            <select className="inp" value={apiFormat} onChange={e => setApiFormat(e.target.value as 'openai' | 'anthropic')}>
              <option value="openai">OpenAI 兼容</option>
              <option value="anthropic">Anthropic Messages（原生）</option>
            </select>
          </Field>
          <Field label="请求地址（Endpoint）">
            <input className="inp" value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder="https://your-api-endpoint.com（不以斜杠结尾）" />
          </Field>
          <Field label="认证环境变量名">
            <input className="inp" value={envKeyVar} onChange={e => setEnvKeyVar(e.target.value)} placeholder="留空则按 {供应商}_API_KEY 生成" />
          </Field>
        </div>

        <Field label="API Key（可选）">
          <input className="inp" type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
            placeholder="填入后仅注入后端进程内存，永不落盘；重启后需重新填写或写入 .env" />
          <div className="sub" style={{ fontSize: 11, marginTop: 4, color: 'var(--amber)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icon name="key" size={12} /> 安全：Key 仅存于后端进程内存（volatile），不写入任何文件/日志/响应（AGENTS.md §12.1）。
          </div>
        </Field>

        {/* 模型列表 */}
        <div className="sub" style={{ fontSize: 12, margin: '10px 0 6px' }}>模型列表 *</div>
        {models.map((m, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
            <input className="inp" style={{ flex: 1 }} value={m.model_name} onChange={e => updateModel(i, 'model_name', e.target.value)} placeholder="模型名称（如 deepseek-chat）" />
            <input className="inp" style={{ flex: 1 }} value={m.display_name} onChange={e => updateModel(i, 'display_name', e.target.value)} placeholder="显示名（可选）" />
            {models.length > 1 && <button className="btn sm ghost" onClick={() => removeModelRow(i)}>✕</button>}
          </div>
        ))}
        <button className="btn sm ghost" onClick={addModelRow} style={{ marginTop: 2 }}>+ 添加模型</button>

        {error && <div className="err" style={{ marginTop: 12, fontSize: 13 }}>⚠ {error}</div>}

        <div className="row" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={submitting}>取消</button>
          <button className="btn" onClick={handleSubmit} disabled={submitting}>
            {submitting ? '添加中…' : '+ 添加'}
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
