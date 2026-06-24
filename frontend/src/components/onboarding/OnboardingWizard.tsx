import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const STEPS = [
  { id: 'env', label: '环境选择', desc: '选择本地环境或远程环境（对应 Environment Profile, D-051）' },
  { id: 'git', label: '提交方式', desc: '本地 Git 或 GitHub 集成' },
  { id: 'model', label: '模型配置', desc: '自动（跟随系统）/ 全局统一 / 自定义（对应 ModelGateway, D-039）' },
  { id: 'mode', label: '执行模式', desc: 'Auto / Plan-Confirm / Manual（D-025）' },
  { id: 'confirm', label: '摘要确认', desc: '确认 4 项选择后进入 Workspace' },
];

interface Props {
  projectId: string;
  projectName: string;
  onDone: () => void;
}

export function OnboardingWizard({ projectId, projectName, onDone }: Props) {
  const [stepIdx, setStepIdx] = useState(0);
  const nav = useNavigate();

  if (stepIdx >= STEPS.length) {
    return (
      <div className="card" style={{ maxWidth: 560, margin: '48px auto', textAlign: 'center' }}>
        <h2>引导完成</h2>
        <p className="sub">项目已就绪，可进入工作区启动 P0-P6 受控流程。</p>
        <span className="tag violet" style={{ marginBottom: 12 }}>Mock</span>
        <div className="row" style={{ justifyContent: 'center' }}>
          <button className="btn" onClick={() => nav(`/projects/${projectId}/workspace`)}>进入工作区</button>
          <button className="btn ghost" onClick={onDone}>返回项目列表</button>
        </div>
      </div>
    );
  }

  const step = STEPS[stepIdx];
  const isLast = stepIdx === STEPS.length - 1;

  return (
    <div style={{ maxWidth: 560, margin: '32px auto' }}>
      <h2>首次引导 · {projectName}</h2>
      <span className="tag violet" style={{ marginBottom: 16 }}>Mock 演示</span>
      <div className="steps">
        {STEPS.map((s, i) => (
          <div key={s.id} className={`step${i < stepIdx ? ' done' : ''}${i === stepIdx ? ' current' : ''}`}>
            {i + 1}. {s.label}
          </div>
        ))}
      </div>
      <div className="card">
        <b>{step.label}</b>
        <p className="sub" style={{ marginTop: 6 }}>{step.desc}</p>
        <span className="tag violet">Mock 选项</span>
        <div style={{ marginTop: 12, fontSize: 13, color: 'var(--ink-2)' }}>
          {step.id === 'env' && <div>默认：本地环境（Mock）</div>}
          {step.id === 'git' && <div>默认：本地 Git（Mock）</div>}
          {step.id === 'model' && <div>默认：自动（跟随系统，Mock）</div>}
          {step.id === 'mode' && <div>默认：Plan-Confirm（Mock）</div>}
          {step.id === 'confirm' && (
            <div>
              <div>✓ 环境：本地环境</div>
              <div>✓ 提交：本地 Git</div>
              <div>✓ 模型：自动</div>
              <div>✓ 模式：Plan-Confirm</div>
            </div>
          )}
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          <button className="btn" onClick={() => setStepIdx(i => i + 1)}>{isLast ? '确认进入' : '下一步'}</button>
          {stepIdx > 0 && <button className="btn ghost" onClick={() => setStepIdx(i => i - 1)}>上一步</button>}
          <button className="btn ghost" onClick={() => { onDone(); }}>跳过引导</button>
        </div>
      </div>
    </div>
  );
}
