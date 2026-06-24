import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useProjectStore } from '../../stores';

export function ProjectCreatePage() {
  const nav = useNavigate();
  const createProject = useProjectStore(s => s.createProject);
  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [sourceType, setSourceType] = useState('local_dir');
  const [err, setErr] = useState('');
  const [created, setCreated] = useState<string | null>(null);

  const sources = [
    ['local_dir', '本地目录', '从平台可访问的本地目录接入'],
    ['git', 'Git 仓库', '通过 Git 仓库 URL 接入'],
    ['zip', 'ZIP 包', '上传或引用 ZIP 包接入'],
    ['github', 'GitHub', '通过 GitHub 仓库接入'],
  ] as const;

  const doCreate = () => {
    if (!name.trim()) { setErr('请填写项目名称'); return; }
    const p = createProject(name);
    setCreated(p.project_id);
    setStep(3);
  };

  return (
    <div>
      <h1>新建项目</h1>
      <p className="sub">创建 Project，接入源码后可进入 P0-P6 受控流程。</p>
      <span className="tag violet" style={{ marginBottom: 12 }}>Mock 演示</span>

      {/* Step Indicator */}
      <div className="steps">
        {['基本信息', '来源配置', '完成'].map((l, i) => (
          <div key={i} className={`step${step > i + 1 ? ' done' : ''}${step === i + 1 ? ' current' : ''}`}>{i + 1}. {l}</div>
        ))}
      </div>

      {err && <div className="redline" style={{ marginBottom: 16 }}>{err}</div>}

      <div className="card">
        {step === 1 && <>
          <h2>基本信息</h2>
          <label>项目名称 *</label>
          <input value={name} onChange={e => { setName(e.target.value); setErr(''); }} placeholder="例如：MicroOA 信创迁移" />
          <label>项目描述（可选）</label>
          <input value={desc} onChange={e => setDesc(e.target.value)} placeholder="简要描述项目迁移目标" />
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" onClick={() => { if (!name.trim()) { setErr('请填写项目名称'); return; } setStep(2); }}>下一步：来源配置</button>
            <button className="btn ghost" onClick={() => nav('/projects')}>取消</button>
          </div>
        </>}

        {step === 2 && <>
          <h2>来源配置</h2>
          <p className="sub">选择源码接入方式（首批支持本地目录/Git/ZIP/GitHub）</p>
          <div className="cardgrid">
            {sources.map(([k, label, desc]) => (
              <div key={k} className={`card${sourceType === k ? ' statcard' : ''}`} style={{ cursor: 'pointer', borderColor: sourceType === k ? 'var(--accent-ink)' : 'var(--line)' }} onClick={() => setSourceType(k)}>
                <b>{label}</b>
                <div className="hash" style={{ marginTop: 4 }}>{desc}</div>
                <span className="tag grey" style={{ marginTop: 8 }}>Mock</span>
              </div>
            ))}
          </div>
          <div className="card" style={{ marginTop: 14 }}>
            <label>源路径 / URL（脱敏展示，Mock 演示）</label>
            <input placeholder="/path/to/source-project" disabled />
            <div className="hash" style={{ marginTop: 4 }}>凭据状态：未配置（Mock）</div>
          </div>
          {/* Feedback Area: Pre-check */}
          <div className="banner info" style={{ marginTop: 12 }}>
            <b>接入前检查（Mock）</b>
            <div style={{ fontSize: 12, marginTop: 4 }}>✓ 项目名称有效 · ✓ 来源类型支持 · ? 凭据未配置（可在 Project 设置中配置）</div>
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" onClick={doCreate}>创建项目</button>
            <button className="btn ghost" onClick={() => setStep(1)}>返回修改</button>
          </div>
        </>}

        {step === 3 && created && <>
          <h2>创建完成</h2>
          <p className="sub">Project 已创建（Mock 演示）。注意：创建成功 ≠ P0 完成。</p>
          <div className="banner info">
            <b>Project ID: {created}</b>
            <div style={{ fontSize: 12, marginTop: 4 }}>导入任务已创建（Mock） · Workspace 已初始化（Mock）</div>
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" onClick={() => nav(`/projects/${created}`)}>打开 Project</button>
            <button className="btn" onClick={() => nav(`/projects/${created}/workspace`)}>进入 Workspace</button>
            <button className="btn ghost" onClick={() => nav('/projects')}>返回项目列表</button>
          </div>
        </>}
      </div>
    </div>
  );
}
