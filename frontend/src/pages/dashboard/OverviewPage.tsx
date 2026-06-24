import { useProjectStore, useRunStore } from '../../stores';
import { useNavigate } from 'react-router-dom';
import { STAGE_LABELS } from '../../types';
import { MockBadge } from '../../components/ui/StatusBadge';
import { Icon, type IconKey } from '../../components/ui/Icon';

export function OverviewPage() {
  const projects = useProjectStore(s => s.projects);
  const runs = useRunStore(s => s.runs);
  const nav = useNavigate();
  const running = runs.filter(r => r.run_status === 'running').length;

  const openWs = (pid: string) => window.open(`/projects/${pid}/workspace`, '_blank');

  const stats = [
    { a: running, b: projects.length, label: '项目（运行 / 总数）', go: '/projects' },
    { a: 0, b: 0, label: '资源（启用 / 总数）', go: '/resources' },
    { a: 0, b: 0, label: '模型（可用 / 总数）', go: '/models' },
    { a: 0, b: 0, label: '集成（启用 / 总数）', go: '/integrations' },
  ];

  const knowledge = [
    { ic: '🚀', title: '平台入门', desc: 'rebuild 平台概览、P0–P6 主轴、Evidence / Artifact / Trace 心智' },
    { ic: '📘', title: '新手教程', desc: '从新建迁移项目到交付的完整流程' },
    { ic: '🧭', title: '信创迁移指南', desc: '.NET / SQL Server → 麒麟 / 达梦 的迁移要点与清单' },
    { ic: '📑', title: 'Evidence 契约速查', desc: 'Evidence v0 字段、类型与引用关系' },
  ];

  // P0–P6 模板预设（导航至创建向导；非真实编排）
  const templates = [
    { title: '完整迁移闭环', flow: 'P0+P1+P2+P3+P4+P5+P6', tag: '推荐' },
    { title: '快速评估', flow: 'P0+P1+P2+P6' },
    { title: '方案设计', flow: 'P0+P1+P2+P3+P6' },
  ];

  const statusBadge = (p: typeof projects[number]) => {
    if (p.project_status === 'running') return <span className="tag green">运行中</span>;
    return <span className="tag blue">就绪</span>;
  };

  const last = projects[0];

  const links = [
    { to: '/community', ic: 'community' as IconKey, title: '社区', ext: true, desc: '案例市场、Skill 市场、模板（新标签页）' },
    { to: '/docs', ic: 'docs' as IconKey, title: '文档', ext: true, desc: '平台使用文档与 Wiki（新标签页）' },
    { to: '/knowledge', ic: 'knowledge' as IconKey, title: '知识', ext: false, desc: '平台自带文档与我的笔记' },
  ];

  return (
    <div>
      <h1>概览</h1>
      <p className="sub">面向软件重构与迁移的工程平台 · 首期聚焦信创迁移 · 用户主路径 P0–P6</p>
      <span className="tag violet" style={{ marginBottom: 8 }}>Mock 数据</span>

      {/* Stat Grid */}
      <div className="statgrid">
        {stats.map((s, i) => (
          <div key={i} className="card statcard" onClick={() => nav(s.go)}>
            <div className="snum">{s.a}<small> / {s.b}</small></div>
            <div className="slabel">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Row 1: 最近知识 | 新建项目 */}
      <div className="cardgrid two">
        <div className="card">
          <div className="spread"><b>最近知识</b><button className="btn sm ghost" onClick={() => nav('/knowledge')}>知识库</button></div>
          <div style={{ marginTop: 4 }}>
            {knowledge.map((k, i) => (
              <div key={i} className="listrow">
                <div className="row" style={{ flexWrap: 'nowrap' }}>
                  <div className="ic">{k.ic}</div>
                  <div>
                    <div className="ttl">{k.title} <span className="tag blue">平台常驻</span></div>
                    <div className="meta">{k.desc}</div>
                  </div>
                </div>
                <MockBadge level="placeholder" />
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <b>新建项目</b>
          <p className="sub" style={{ marginTop: 6, marginBottom: 8 }}>选择 P0–P6 模板，进入项目创建向导。</p>
          {templates.map((t, i) => (
            <div key={i} className="listrow" style={{ cursor: 'pointer' }} onClick={() => nav('/projects/new')}>
              <div>
                <div className="ttl">{t.title} {t.tag && <span className="tag amber">{t.tag}</span>}</div>
                <div className="meta mono">{t.flow}</div>
              </div>
              <span style={{ color: 'var(--ink-3)' }}>→</span>
            </div>
          ))}
          <button className="btn" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }} onClick={() => nav('/projects/new')}>＋ 新建迁移项目</button>
        </div>
      </div>

      {/* Row 2: 最近项目 | 上次退出的项目 */}
      <div className="cardgrid two">
        <div className="card">
          <div className="spread"><b>最近项目</b><button className="btn sm ghost" onClick={() => nav('/projects')}>查看全部</button></div>
          <div style={{ marginTop: 4 }}>
            {projects.slice(0, 4).map(p => (
              <div key={p.project_id} className="listrow">
                <div>
                  <div className="ttl">
                    {p.name} {statusBadge(p)}
                    {p.active_gate && <span className="tag amber">Waiting Gate</span>}
                  </div>
                  <div className="meta">{p.description} · {p.current_stage ? STAGE_LABELS[p.current_stage] : '未开始'}</div>
                </div>
                <button className="btn sm" onClick={() => openWs(p.project_id)}>打开工作区</button>
              </div>
            ))}
            {!projects.length && <div className="empty" style={{ padding: '12px 0' }}>暂无项目，去「项目」页新建</div>}
          </div>
        </div>

        <div className="card">
          <div className="spread"><b>上次退出的项目</b><MockBadge level="mock" /></div>
          {last ? (
            <div style={{ marginTop: 8 }}>
              <div className="ttl" style={{ fontSize: 14 }}>{last.name}</div>
              <div style={{ marginTop: 8 }}>
                <div className="kv"><span className="k">当前阶段</span><span className="v">{last.current_stage ? STAGE_LABELS[last.current_stage] : '未开始'}</span></div>
                <div className="kv"><span className="k">Evidence 缺口</span><span className="v">{last.evidence_gap_count}</span></div>
                <div className="kv"><span className="k">源类型</span><span className="v">{last.source_type}</span></div>
                <div className="kv"><span className="k">Gate</span><span className="v">{last.active_gate ? '等待决策' : '无'}</span></div>
              </div>
              <div className="banner info" style={{ marginTop: 10, fontSize: 12 }}>
                提示文案与进度为 Mock，未接入真实 Run 状态。
              </div>
              <button className="btn" style={{ width: '100%', justifyContent: 'center', marginTop: 10 }} onClick={() => openWs(last.project_id)}>▶ 继续此项目</button>
            </div>
          ) : <div className="empty">暂无历史项目</div>}
        </div>
      </div>

      {/* Row 3: 社区 / 文档 / 知识 */}
      <div className="cardgrid three">
        {links.map(l => (
          <div key={l.to} className="card statcard" onClick={() => l.ext ? window.open(l.to, '_blank') : nav(l.to)}>
            <div className="ttl" style={{ fontSize: 14 }}>
              <span className="ic" style={{ color: 'var(--ink-2)', display: 'inline-flex' }}><Icon name={l.ic} size={18} /></span>
              {l.title}
              {l.ext && <span style={{ color: 'var(--ink-3)', fontWeight: 400, display: 'inline-flex' }}><Icon name="externalLink" size={13} /></span>}
            </div>
            <div className="meta" style={{ marginTop: 6 }}>{l.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
