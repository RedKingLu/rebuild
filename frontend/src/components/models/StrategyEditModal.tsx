/** StrategyEditModal — 编辑模型策略（R5-4）：默认模型 + fallback 链。
 * 仅开放 default_profile_ref / fallback_profile_refs 两项（其余策略字段 R5 不开放）。
 * 覆盖落盘 user_strategies.yaml（非敏感）。中文优先；线性图标。
 */
import { useState, type ReactNode } from 'react';
import { updateStrategy, type StrategyInfo, type ModelProfileInfo } from '../../services/modelService';
import { Icon } from '../ui/Icon';

interface Props {
  strategy: StrategyInfo;
  profiles: ModelProfileInfo[];
  onClose: () => void;
  onSaved: () => void;
}

export function StrategyEditModal({ strategy, profiles, onClose, onSaved }: Props) {
  const [defaultRef, setDefaultRef] = useState(strategy.default_profile_ref);
  const [fallbacks, setFallbacks] = useState<string[]>(strategy.fallback_profile_refs || []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleFallback = (pid: string) => {
    setFallbacks(prev => prev.includes(pid) ? prev.filter(x => x !== pid) : [...prev, pid]);
  };

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const resp = await updateStrategy(strategy.strategy_id, {
        default_profile_ref: defaultRef,
        fallback_profile_refs: fallbacks,
      });
      if (!resp.data) { setError('保存失败：策略不存在'); return; }
      onSaved(); onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }} onClick={onClose}>
      <div className="card" style={{ width: 'min(560px, 96vw)', maxHeight: '90vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
        <div className="spread" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>编辑策略：{strategy.strategy_id === 'system-default' ? '系统默认' : strategy.strategy_id}</h2>
          <button className="btn sm ghost" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button>
        </div>

        <Field label="默认模型">
          <select className="inp" value={defaultRef} onChange={e => setDefaultRef(e.target.value)}>
            <option value="">（未设置）</option>
            {profiles.map(p => (
              <option key={p.profile_id} value={p.profile_id} disabled={p.status !== 'configured'}>
                {p.display_name}（{p.profile_id}）{p.status !== 'configured' ? ' · 未配置' : ''}
              </option>
            ))}
          </select>
        </Field>

        <div style={{ marginTop: 12 }}>
          <label className="sub" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>Fallback 链（默认不可用时按勾选顺序回退）</label>
          <div style={{ display: 'grid', gap: 4, maxHeight: 240, overflow: 'auto' }}>
            {profiles.map(p => {
              const idx = fallbacks.indexOf(p.profile_id);
              return (
                <label key={p.profile_id} className="row" style={{ gap: 8, fontSize: 13, padding: '4px 6px', border: '1px solid var(--line)', borderRadius: 6, cursor: 'pointer' }}>
                  <input type="checkbox" checked={idx >= 0} onChange={() => toggleFallback(p.profile_id)} />
                  <span style={{ flex: 1 }}>{p.display_name} <span className="sub" style={{ fontSize: 11 }}>{p.profile_id}</span></span>
                  {idx >= 0 && <span className="tag" style={{ fontSize: 11 }}>第 {idx + 1} 顺位</span>}
                </label>
              );
            })}
          </div>
        </div>

        {error && <div className="err" style={{ marginTop: 12, fontSize: 13 }}>⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={save} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
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
