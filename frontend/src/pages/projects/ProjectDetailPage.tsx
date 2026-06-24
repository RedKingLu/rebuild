import { useParams, useNavigate } from 'react-router-dom';
import { useProjectStore, useRunStore, useGateStore, useEvidenceStore } from '../../stores';
import { STAGE_LABELS, type StageId } from '../../types';

const STAGES: StageId[] = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6'];

// Project area = 仅创建与展示（D-046）。Stage / Run / 产物 / 证据 / Trace / Audit / Gate / 设置
// 等深度交互全部在 Workspace 中进行，不在项目区重复入口。
export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const project = useProjectStore(s => id ? s.getProject(id) : undefined);
  const runs = useRunStore(s => id ? s.getRunsByProject(id) : []);
  const activeGate = useGateStore(s => id ? s.getActiveGate(id) : undefined);
  const gaps = useEvidenceStore(s => s.gaps);

  if (!project) return <div className="empty">Project 未找到</div>;

  const run = runs[0] || null;
  const ss = run?.stage_status || {};
  const enterWs = () => nav(`/projects/${id}/workspace`);

  return (
    <div>
      <div className="spread">
        <h1>{project.name}</h1>
        <div className="row">
          <span className="tag violet">Mock</span>
          <button className="btn" onClick={enterWs}>进入工作区</button>
        </div>
      </div>
      <p className="sub">{project.description || '（无描述）'}</p>

      <div className="banner info" style={{ marginBottom: 16, fontSize: 12 }}>
        项目区仅用于创建与展示。阶段推进、Run、产物 / 证据 / Trace / Audit、Gate 决策与设置均在工作区内完成（D-046）。
      </div>

      {/* P0-P6 Mini Progress（只读展示） */}
      <div className="row" style={{ marginBottom: 18, gap: 2 }}>
        {STAGES.map((s) => {
          const st = ss[s] || 'pending';
          const colors: Record<string, string> = { completed: 'var(--green)', in_progress: 'var(--blue)', waiting_gate: 'var(--amber)', blocked: 'var(--red)', failed: 'var(--red)', pending: 'var(--line)', not_enabled: 'var(--line-2)', skipped: 'var(--line-2)' };
          return <div key={s} style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ height: 6, borderRadius: 3, background: colors[st] || 'var(--line)', marginBottom: 4 }} />
            <div style={{ fontSize: 10, color: st === 'in_progress' ? 'var(--accent-ink)' : 'var(--ink-3)', fontWeight: st === 'in_progress' ? 700 : 400 }}>{STAGE_LABELS[s].split(' ')[0]}</div>
          </div>;
        })}
      </div>

      {/* 只读概览卡片：点击任意卡片进入工作区对应视图 */}
      <div className="cardgrid">
        <div className="card statcard" onClick={enterWs}>
          <b>当前阶段 / Run</b>
          {run ? (
            <div style={{ marginTop: 8, fontSize: 13 }}>
              <div className="hash">阶段: {STAGE_LABELS[run.current_stage]}</div>
              <div className="hash">状态: <span className={`tag ${run.run_status === 'running' ? 'green' : run.run_status === 'waiting_gate' ? 'amber' : 'grey'}`}>{run.run_status}</span></div>
            </div>
          ) : <div className="empty" style={{ padding: '12px 0', fontSize: 12 }}>无运行中任务</div>}
        </div>

        <div className="card statcard" onClick={enterWs}>
          <b>Active Gate</b>
          {activeGate ? (
            <div style={{ marginTop: 8 }}>
              <span className="tag amber">{activeGate.gate_id}</span>
              <div style={{ fontSize: 13, marginTop: 6 }}>{activeGate.summary}</div>
            </div>
          ) : <div className="empty" style={{ padding: '12px 0', fontSize: 12 }}>无 Active Gate</div>}
        </div>

        <div className="card statcard" onClick={enterWs}>
          <b>Evidence 缺口</b>
          <div style={{ fontSize: 13, marginTop: 8 }}>{gaps.length} 个缺口（Mock）</div>
          <div className="hash" style={{ marginTop: 6 }}>在工作区右侧检视中查看证据链</div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <b>接入来源</b>
        <div className="hash" style={{ marginTop: 6 }}>source_type: {project.source_type} · workspace: {project.workspace_status} · 引导: {project.onboarding_done ? '已完成' : '未完成'}</div>
      </div>
    </div>
  );
}
