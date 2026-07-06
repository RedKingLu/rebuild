/** 配置面板（WP-7.2 子 tab 2 详情）：Panel / Judge / Synthesizer / 全局。
 * 遵循 09-聚合页 §4 分区；synthesizer 不含工具配置（方案 E）；提交前做前端预检。 */
import { useEffect, useState } from 'react';
import { listProfiles, type ModelProfileInfo } from '../../../services/modelService';
import {
  type FusionProfile, type FusionProfileUpdate,
  type PanelParticipant, type JudgeConfig, type SynthesizerConfig,
} from '../../../services/fusionService';
import { Icon } from '../../../components/ui/Icon';

const PERSPECTIVES = ['general', 'architecture', 'security', 'performance', 'maintainability', 'compliance'];

export function FusionConfigPanel({ profile, onSaved, onClose }: {
  profile: FusionProfile;
  onSaved: (body: FusionProfileUpdate) => Promise<void>;
  onClose: () => void;
}) {
  const [profiles, setProfiles] = useState<ModelProfileInfo[]>([]);
  const [panel, setPanel] = useState<PanelParticipant[]>(
    (profile.panel_participants as unknown as PanelParticipant[]) || []);
  const [judge, setJudge] = useState<JudgeConfig>(
    (profile.judge as unknown as JudgeConfig) || { profile_ref: '', temperature: 0 });
  const [synthesizer, setSynthesizer] = useState<SynthesizerConfig>(
    (profile.synthesizer as unknown as SynthesizerConfig) || { profile_ref: '' });
  const [name, setName] = useState(profile.name || '');
  const [style, setStyle] = useState(profile.style || 'balanced');
  const [trigger, setTrigger] = useState(profile.trigger || 'manual');
  const [enabledStages, setEnabledStages] = useState<string[]>(profile.enabled_stages || []);
  const [timeout, setTimeoutVal] = useState(profile.timeout_seconds || 120);
  const [selfMoa, setSelfMoa] = useState(profile.self_moa_enabled !== false);
  const [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState<{ errors: string[]; warnings: string[] } | null>(null);

  useEffect(() => {
    // R13-8-FIX: 正确解包 envelope；并过滤掉 rebuild-fusion 虚拟模型——Fusion 不得作为
    // 参与/Judge/Synthesizer 候选（防递归三重保险之一，后端 validate 亦拒绝 is_fusion 参与者）。
    listProfiles()
      .then(r => setProfiles((r.data?.profiles || []).filter(p => p.provider_id !== 'rebuild-fusion')))
      .catch(() => {});
  }, []);

  function updatePanel(i: number, patch: Partial<PanelParticipant>) {
    setPanel(prev => prev.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }
  function addPanel() {
    const first = profiles[0];
    setPanel(prev => [...prev, { profile_ref: first ? first.profile_id : '', perspective: 'general' }]);
  }
  function removePanel(i: number) { setPanel(prev => prev.filter((_, idx) => idx !== i)); }

  function toggleStage(s: string) {
    setEnabledStages(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  }

  async function handleSave() {
    setSaving(true); setValidation(null);
    const body: FusionProfileUpdate = {
      name: name.trim() || profile.name,
      panel_participants: panel,
      judge: { profile_ref: judge.profile_ref, temperature: 0 },
      synthesizer: { profile_ref: synthesizer.profile_ref },
      global_config: { style, trigger, enabled_stages: enabledStages, timeout_seconds: timeout, self_moa_enabled: selfMoa },
    };
    try {
      // R13-8-FIX (F4): 直接提交编辑内容；后端 update 对**所编辑的 body** 做二次校验
      // （防递归 / 异构 / 数量 / 无工具），非法则返回 422。据此如实展示错误——
      // 不再校验旧存储态（原 bug：validate 的是 profile.fusion_profile_id 的历史配置）。
      await onSaved(body);
    } catch (e) {
      setValidation({ errors: [(e as Error).message || '保存失败'], warnings: [] });
    } finally { setSaving(false); }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {validation && (validation.errors.length > 0 || validation.warnings.length > 0) && (
        <div>
          {validation.errors.map((e, i) => (
            <div key={`e${i}`} className="banner warn" style={{ marginBottom: 6 }}>⚠ {e}</div>
          ))}
          {validation.warnings.map((w, i) => (
            <div key={`w${i}`} className="banner info" style={{ marginBottom: 6, fontSize: 12 }}>ℹ {w}</div>
          ))}
        </div>
      )}

      {/* 名称 */}
      <section>
        <b style={{ fontSize: 13 }}>聚合模型名称</b>
        <input value={name} onChange={e => setName(e.target.value)}
          placeholder="为这个聚合模型起个名字，用于在模型列表与触发历史中识别"
          style={{ width: '100%', padding: '6px 8px', fontSize: 13, boxSizing: 'border-box', marginTop: 6 }} />
      </section>

      {/* Panel */}
      <section>
        <b style={{ fontSize: 13 }}>Panel（多模型审议）</b>
        {panel.length === 0 && <div className="empty"><p className="sub">暂无参与模型，点击下方添加。</p></div>}
        <div style={{ display: 'grid', gap: 8, marginTop: 6 }}>
          {panel.map((p, i) => (
            <div key={i} className="row" style={{ gap: 8, alignItems: 'center' }}>
              <select value={p.profile_ref}
                onChange={e => updatePanel(i, { profile_ref: e.target.value })}
                style={{ flex: 1, padding: '5px 8px', fontSize: 12 }}>
                <option value="">选择模型…</option>
                {profiles.map(mp => <option key={mp.profile_id} value={mp.profile_id}>{mp.profile_id}</option>)}
              </select>
              <select value={p.perspective || 'general'}
                onChange={e => updatePanel(i, { perspective: e.target.value as any })}>
                {PERSPECTIVES.map(ps => <option key={ps} value={ps}>{ps}</option>)}
              </select>
              <button className="btn sm ghost" onClick={() => removePanel(i)} title="移除"><Icon name="delete" size={13} /></button>
            </div>
          ))}
        </div>
        <button className="btn sm ghost" onClick={addPanel} style={{ marginTop: 8 }}>
          <Icon name="add" size={13} />添加参与模型
        </button>
      </section>

      {/* Judge */}
      <section>
        <b style={{ fontSize: 13 }}>Judge（评判，temperature=0）</b>
        <div className="row" style={{ gap: 8, marginTop: 6, alignItems: 'center' }}>
          <select value={judge.profile_ref}
            onChange={e => setJudge({ ...judge, profile_ref: e.target.value })}
            style={{ flex: 1, padding: '5px 8px', fontSize: 12 }}>
            <option value="">选择评判模型…</option>
            {profiles.map(mp => <option key={mp.profile_id} value={mp.profile_id}>{mp.profile_id}</option>)}
          </select>
          <span className="tag" style={{ fontSize: 11 }}>temp = 0（结构化评分）</span>
        </div>
      </section>

      {/* Synthesizer */}
      <section>
        <b style={{ fontSize: 13 }}>Synthesizer（综合，无工具配置 — 方案 E）</b>
        <div className="row" style={{ gap: 8, marginTop: 6, alignItems: 'center' }}>
          <select value={synthesizer.profile_ref}
            onChange={e => setSynthesizer({ ...synthesizer, profile_ref: e.target.value })}
            style={{ flex: 1, padding: '5px 8px', fontSize: 12 }}>
            <option value="">选择综合模型…</option>
            {profiles.map(mp => <option key={mp.profile_id} value={mp.profile_id}>{mp.profile_id}</option>)}
          </select>
          <span className="tag" style={{ fontSize: 11 }}>不执行工具</span>
        </div>
      </section>

      {/* 全局配置 */}
      <section>
        <b style={{ fontSize: 13 }}>全局配置</b>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 }}>
          <div>
            <div className="sub" style={{ fontSize: 11, marginBottom: 3 }}>风格</div>
            <select value={style} onChange={e => setStyle(e.target.value)} style={{ width: '100%', padding: '5px 8px' }}>
              <option value="budget">保守 (budget)</option>
              <option value="balanced">平衡</option>
              <option value="frontier">激进 (frontier)</option>
            </select>
          </div>
          <div>
            <div className="sub" style={{ fontSize: 11, marginBottom: 3 }}>触发方式</div>
            <select value={trigger} onChange={e => setTrigger(e.target.value)} style={{ width: '100%', padding: '5px 8px' }}>
              <option value="manual">手动触发</option>
              <option value="auto">阶段自动</option>
            </select>
          </div>
          <div>
            <div className="sub" style={{ fontSize: 11, marginBottom: 3 }}>超时（秒）</div>
            <input type="number" value={timeout} min={10} max={600}
              onChange={e => setTimeoutVal(Number(e.target.value))}
              style={{ width: '100%', padding: '5px 8px', boxSizing: 'border-box' }} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <label className="row" style={{ gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={selfMoa} onChange={e => setSelfMoa(e.target.checked)} />
              <span className="sub" style={{ fontSize: 12 }}>启用 Self-MoA 降级</span>
            </label>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="sub" style={{ fontSize: 11, marginBottom: 3 }}>启用阶段</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {['p2', 'p3', 'p5', 'p6'].map(s => (
              <button key={s} className={`btn sm ${enabledStages.includes(s) ? '' : 'ghost'}`}
                onClick={() => toggleStage(s)}>P{s.slice(1)}</button>
            ))}
          </div>
        </div>
      </section>

      <div className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
        <button className="btn sm ghost" onClick={onClose}>取消</button>
        <button className="btn sm" disabled={saving} onClick={handleSave}>
          <Icon name="key" size={13} />{saving ? '校验中…' : '保存配置'}
        </button>
      </div>
    </div>
  );
}
