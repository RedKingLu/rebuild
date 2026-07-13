import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { fetchProject } from '../../services/projectService';
import { fetchRuns } from '../../services/runService';
import { fetchActiveGate } from '../../services/gateService';
import { fetchEvidence } from '../../services/aetService';
import { STAGE_LABELS, type Project, type Run, type Gate, type Evidence, type StageId } from '../../types';

const STAGES: StageId[] = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6'];

// Project area = 仅创建与展示（D-046）。阶段推进 / Run 控制 / 产物 / 证据 / Trace / Audit /
// Gate 决策 / 设置等深度交互全部在 Workspace 中进行，不在项目区重复入口。
// 本页仅以「只读摘要」呈现：其中 Active Gate 只读摘要为 D-023 跨层可见性要求，决策在
// Workspace 内完成。所有数据均来自真实后端 API（D-097：不使用、不回退 mock）。
export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // 只读业务摘要 — 真实 API（runs / active gate / evidence）
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeGate, setActiveGate] = useState<Gate | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [auxError, setAuxError] = useState('');

  // shouldApply：竞态守卫。id 快速切换 / 组件卸载时，旧请求的响应不得覆盖新数据。
  const load = useCallback(async (shouldApply: () => boolean = () => true) => {
    if (!id) return;
    setLoading(true);
    setError('');
    setAuxError('');
    // 主数据：项目信息。失败即显式错误态 + 可重试，不回退 mock。
    let proj: Project;
    try {
      proj = await fetchProject(id);
    } catch (e: unknown) {
      if (shouldApply()) {
        setError(e instanceof Error ? e.message : '加载项目失败');
        setLoading(false);
      }
      return;
    }
    if (!shouldApply()) return;
    setProject(proj);
    // 次要只读摘要：并行拉取，任一失败仅提示该区块可重试，不阻断项目展示、不回退 mock。
    const [runsR, gateR, evR] = await Promise.allSettled([
      fetchRuns(id),
      fetchActiveGate(id),
      fetchEvidence(id),
    ]);
    if (!shouldApply()) return;
    setRuns(runsR.status === 'fulfilled' ? runsR.value : []);
    setActiveGate(gateR.status === 'fulfilled' ? gateR.value : null);
    setEvidence(evR.status === 'fulfilled' ? evR.value : []);
    if (runsR.status === 'rejected' || gateR.status === 'rejected' || evR.status === 'rejected') {
      setAuxError('运行 / Gate / 证据摘要部分加载失败');
    }
    setLoading(false);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    load(() => !cancelled);
    return () => { cancelled = true; };
  }, [load]);

  const enterWs = () => window.open(`/projects/${id}/workspace`);

  if (loading) {
    return (
      <div>
        <h1>项目详情</h1>
        <div className="empty">加载中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1>项目详情</h1>
        <div className="redline" style={{ marginBottom: 16 }}>{error}</div>
        <button className="btn ghost" onClick={() => load()}>
          重试
        </button>
      </div>
    );
  }

  if (!project) {
    return (
      <div>
        <h1>项目详情</h1>
        <div className="empty">Project 未找到</div>
      </div>
    );
  }

  // 当前 Run：优先匹配 project.current_run_id，否则取列表首项
  const run =
    (project.current_run_id ? runs.find((r) => r.run_id === project.current_run_id) : undefined) ||
    runs[0] ||
    null;
  const ss = run?.stage_status || {};
  const gapCount = evidence.filter(
    (e) => e.blocking || e.evidence_status === 'insufficient',
  ).length;

  return (
    <div>
      <div className="spread">
        <h1>{project.name}</h1>
        <div className="row">
          <span className={`tag ${project.project_status === 'ready' ? 'green' : project.project_status === 'running' ? 'blue' : project.project_status === 'error' ? 'red' : 'grey'}`}>
            {project.project_status}
          </span>
          <button className="btn" onClick={enterWs}>打开工作区</button>
        </div>
      </div>
      <p className="sub">{project.description || '（无描述）'}</p>

      <div className="banner info" style={{ marginBottom: 16, fontSize: 12 }}>
        项目区仅用于创建与展示。阶段推进、Run、产物 / 证据 / Trace / Audit、Gate 决策与设置均在工作区内完成（D-046）。
      </div>

      {auxError && (
        <div className="banner warn" style={{ marginBottom: 16, fontSize: 12 }}>
          {auxError}
          <button className="btn ghost" style={{ marginLeft: 12 }} onClick={() => load()}>
            重试
          </button>
        </div>
      )}

      {/* Project metadata — real API data */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <b>项目信息</b>
          <span className="tag green">真实 API</span>
        </div>
        <div className="hash" style={{ marginTop: 8 }}>
          <div>Project ID: {project.project_id}</div>
          <div>状态: {project.project_status}</div>
          <div>来源类型: {project.source_type}</div>
          {project.source_config && Object.keys(project.source_config).length > 0 && (
            <div>来源配置: {JSON.stringify(project.source_config)}</div>
          )}
          {project.created_at && <div>创建时间: {project.created_at}</div>}
          {project.updated_at && <div>更新时间: {project.updated_at}</div>}
          <div>WorkSpace: {project.workspace_status}</div>
        </div>
      </div>

      {/* P0-P6 Mini Progress（只读展示 — 真实 Run.stage_status） */}
      <div className="row" style={{ marginBottom: 18, gap: 2 }}>
        {STAGES.map((s) => {
          const st = ss[s] || 'pending';
          const colors: Record<string, string> = {
            completed: 'var(--green)',
            in_progress: 'var(--blue)',
            waiting_gate: 'var(--amber)',
            blocked: 'var(--red)',
            failed: 'var(--red)',
            pending: 'var(--line)',
            not_enabled: 'var(--line-2)',
            skipped: 'var(--line-2)',
          };
          return (
            <div key={s} style={{ flex: 1, textAlign: 'center' }}>
              <div
                style={{
                  height: 6,
                  borderRadius: 3,
                  background: colors[st] || 'var(--line)',
                  marginBottom: 4,
                }}
              />
              <div
                style={{
                  fontSize: 10,
                  color: st === 'in_progress' ? 'var(--accent-ink)' : 'var(--ink-3)',
                  fontWeight: st === 'in_progress' ? 700 : 400,
                }}
              >
                {STAGE_LABELS[s].split(' ')[0]}
              </div>
            </div>
          );
        })}
      </div>

      {/* 只读概览卡片：点击任意卡片进入工作区对应视图 */}
      <div className="cardgrid">
        <div className="card statcard" onClick={enterWs}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <b>当前阶段 / Run</b>
            <span className="tag green">真实 API</span>
          </div>
          {run ? (
            <div style={{ marginTop: 8, fontSize: 13 }}>
              <div className="hash">阶段: {STAGE_LABELS[run.current_stage]}</div>
              <div className="hash">
                状态:{' '}
                <span
                  className={`tag ${run.run_status === 'running' ? 'green' : run.run_status === 'waiting_gate' ? 'amber' : 'grey'}`}
                >
                  {run.run_status}
                </span>
              </div>
            </div>
          ) : (
            <div className="empty" style={{ padding: '12px 0', fontSize: 12 }}>
              无运行中任务
            </div>
          )}
        </div>

        <div className="card statcard" onClick={enterWs}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <b>Active Gate</b>
            <span className="tag green">真实 API</span>
          </div>
          {activeGate ? (
            <div style={{ marginTop: 8 }}>
              <span className="tag amber">{activeGate.gate_id}</span>
              <div style={{ fontSize: 13, marginTop: 6 }}>{activeGate.summary}</div>
              <div className="hash" style={{ marginTop: 6 }}>决策在工作区内完成（D-023）</div>
            </div>
          ) : (
            <div className="empty" style={{ padding: '12px 0', fontSize: 12 }}>
              无 Active Gate
            </div>
          )}
        </div>

        <div className="card statcard" onClick={enterWs}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <b>Evidence 缺口</b>
            <span className="tag green">真实 API</span>
          </div>
          <div style={{ fontSize: 13, marginTop: 8 }}>{gapCount} 个缺口</div>
          <div className="hash" style={{ marginTop: 6 }}>
            在工作区右侧检视中查看证据链
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <b>接入来源</b>
          <span className="tag green">真实 API</span>
        </div>
        <div className="hash" style={{ marginTop: 6 }}>
          source_type: {project.source_type} · workspace: {project.workspace_status} · 引导:{' '}
          {project.onboarding_done ? '已完成' : '未完成'}
        </div>
      </div>
    </div>
  );
}
