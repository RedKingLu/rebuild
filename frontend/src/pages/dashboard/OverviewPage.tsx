import { useNavigate } from 'react-router-dom';
import { STAGE_LABELS } from '../../types';
import type { Project } from '../../types';
import { Icon, type IconKey } from '../../components/ui/Icon';
import { useState, useEffect } from 'react';
import { fetchProjects, fetchProject } from '../../services/projectService';
import { listResources } from '../../services/resourceService';
import { listAgents } from '../../services/agentService';
import { getModelStatus } from '../../services/modelService';
import { getIntegrationSummary } from '../../services/integrationService';

export function OverviewPage() {
  const nav = useNavigate();

  // ── Stat[0]: 项目（运行 / 总数）──
  const [projRunning, setProjRunning] = useState(0);
  const [projTotal, setProjTotal] = useState(0);
  const [projLoading, setProjLoading] = useState(true);
  const [projError, setProjError] = useState('');

  // ── Stat[1]: 资源（Agent / 总数）──
  const [agentCount, setAgentCount] = useState(0);
  const [resTotal, setResTotal] = useState(0);
  const [resLoading, setResLoading] = useState(true);
  const [resError, setResError] = useState('');

  // ── Stat[2]: 模型（可用 / 总数）──
  const [modelAvail, setModelAvail] = useState(0);
  const [modelTotal, setModelTotal] = useState(0);
  const [modelLoading, setModelLoading] = useState(true);
  const [modelError, setModelError] = useState('');

  // ── Stat[3]: 集成（已接入 / 总数）──
  const [intConnected, setIntConnected] = useState(0);
  const [intTotal, setIntTotal] = useState(0);
  const [intLoading, setIntLoading] = useState(true);
  const [intError, setIntError] = useState('');

  // ── Row 1 left (2/3): 上次退出项目 ──
  const [lastProject, setLastProject] = useState<Project | null>(null);
  const [lastLoading, setLastLoading] = useState(true);
  const [lastError, setLastError] = useState('');

  // ── Row 2 left: 最近项目 (≤3) ──
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState('');

  // ── Row 2 right: 最近知识 (≤3) ──
  const [knowledgeItems, setKnowledgeItems] = useState<{ name: string; desc: string }[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(true);
  const [knowledgeError, setKnowledgeError] = useState('');

  useEffect(() => {
    // ── Stat[0] + Row 2 最近项目: fetchProjects (共享一次调用) ──
    setProjLoading(true);
    setRecentLoading(true);
    fetchProjects()
      .then((ps) => {
        const running = ps.filter((p) => p.project_status === 'running').length;
        setProjRunning(running);
        setProjTotal(ps.length);
        setProjLoading(false);

        const sorted = [...ps]
          .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
          .slice(0, 3);
        setRecentProjects(sorted);
        setRecentLoading(false);
      })
      .catch((e) => {
        const msg = e?.message || '加载项目失败';
        setProjError(msg);
        setProjLoading(false);
        setRecentError(msg);
        setRecentLoading(false);
      });

    // ── Stat[1]: Agents + Resources ──
    setResLoading(true);
    Promise.all([
      listAgents()
        .then((r) => setAgentCount(r.total || r.agents?.length || 0))
        .catch(() => {}),
      listResources()
        .then((r) => setResTotal(r.total || r.resources?.length || 0))
        .catch(() => {}),
    ])
      .then(() => setResLoading(false))
      .catch(() => {
        setResError('加载资源失败');
        setResLoading(false);
      });

    // ── Stat[2]: Model status ──
    setModelLoading(true);
    getModelStatus()
      .then((r) => {
        const d = r.data;
        setModelAvail(d?.configured_profiles ?? 0);
        setModelTotal(d?.total_profiles ?? 0);
        setModelLoading(false);
      })
      .catch((e) => {
        setModelError(e?.message || '加载模型状态失败');
        setModelLoading(false);
      });

    // ── Stat[3]: Integration summary ──
    setIntLoading(true);
    getIntegrationSummary()
      .then((s) => {
        const connected =
          (s.git?.connected ?? 0) +
          (s.remote?.connected ?? 0) +
          (s.coding_agents?.connected ?? 0) +
          (s.other?.connected ?? 0);
        const total =
          (s.git?.total ?? 0) +
          (s.remote?.total ?? 0) +
          (s.coding_agents?.total ?? 0) +
          (s.other?.total ?? 0);
        setIntConnected(connected);
        setIntTotal(total);
        setIntLoading(false);
      })
      .catch((e) => {
        setIntError(e?.message || '加载集成状态失败');
        setIntLoading(false);
      });

    // ── Row 1 left: 上次退出项目（localStorage → fetchProject）──
    const lastId = localStorage.getItem('lastProjectId');
    if (lastId) {
      setLastLoading(true);
      fetchProject(lastId)
        .then((p) => {
          setLastProject(p);
          setLastLoading(false);
        })
        .catch((e) => {
          setLastError(e?.message || '加载项目失败');
          setLastLoading(false);
        });
    } else {
      setLastLoading(false);
    }

    // ── Row 2 right: 最近知识 ──
    setKnowledgeLoading(true);
    listResources({ type: 'knowledge', limit: 3 })
      .then((r) => {
        const items = (r.resources || []).map((k) => ({
          name: k.name,
          desc: k.description?.slice(0, 80) || '暂无摘要',
        }));
        setKnowledgeItems(items);
        setKnowledgeLoading(false);
      })
      .catch((e) => {
        setKnowledgeError(e?.message || '加载知识库失败');
        setKnowledgeLoading(false);
      });
  }, []);

  const openWs = (pid: string) => window.open(`/projects/${pid}/workspace`, '_blank');

  // ── 模板预设 ──
  const templates = [
    { title: '完整迁移闭环', flow: 'P0+P1+P2+P3+P4+P5+P6', tag: '推荐' },
    { title: '快速评估', flow: 'P0+P1+P2+P6' },
    { title: '方案设计', flow: 'P0+P1+P2+P3+P6' },
  ];

  const statusBadge = (p: Project) => {
    if (p.project_status === 'running') return <span className="tag green">运行中</span>;
    return <span className="tag blue">就绪</span>;
  };

  // ── Row 3 底部链接（保持不变）──
  const links = [
    { to: '/community', ic: 'community' as IconKey, title: '社区', ext: true, desc: '案例市场、Skill 市场、模板（新标签页）' },
    { to: '/docs', ic: 'docs' as IconKey, title: '文档', ext: true, desc: '平台使用文档与 Wiki（新标签页）' },
    { to: '/knowledge', ic: 'knowledge' as IconKey, title: '知识', ext: false, desc: '平台自带文档与我的笔记' },
  ];

  // ── Stat card helper ──
  interface StatDef {
    valueA: number;
    valueB: number;
    label: string;
    go: string;
    loading: boolean;
    error: string;
  }

  const stats: StatDef[] = [
    { valueA: projRunning, valueB: projTotal, label: '项目（运行 / 总数）', go: '/projects', loading: projLoading, error: projError },
    { valueA: agentCount, valueB: resTotal, label: '资源（Agent / 总数）', go: '/resources', loading: resLoading, error: resError },
    { valueA: modelAvail, valueB: modelTotal, label: '模型（可用 / 总数）', go: '/models', loading: modelLoading, error: modelError },
    { valueA: intConnected, valueB: intTotal, label: '集成（已接入 / 总数）', go: '/integrations', loading: intLoading, error: intError },
  ];

  return (
    <div>
      <h1>概览</h1>
      <p className="sub">面向软件重构与迁移的工程平台 · 首期聚焦信创迁移 · 用户主路径 P0–P6</p>
      <span className="tag" style={{ marginBottom: 8, background: 'var(--green)', color: '#fff' }}>真实数据</span>

      {/* ═══ Stat Grid (4 cards) ═══ */}
      <div className="statgrid">
        {stats.map((s, i) => (
          <div
            key={i}
            className="card statcard"
            onClick={() => {
              if (!s.loading && !s.error) nav(s.go);
            }}
            style={{ cursor: s.loading || s.error ? 'default' : 'pointer' }}
          >
            {s.loading ? (
              <div className="snum" style={{ fontSize: 16 }}>加载中...</div>
            ) : s.error ? (
              <>
                <div className="snum" style={{ color: 'var(--red)', fontSize: 14 }}>加载失败</div>
                <div className="slabel" style={{ color: 'var(--red)' }}>{s.error}</div>
              </>
            ) : (
              <>
                <div className="snum">{s.valueA}<small> / {s.valueB}</small></div>
                <div className="slabel">{s.label}</div>
              </>
            )}
          </div>
        ))}
      </div>

      {/* ═══ Row 1: 上次退出项目 (2/3) | 新建项目 (1/3) ═══ */}
      <div className="cardgrid" style={{ gridTemplateColumns: '2fr 1fr' }}>
        {/* ── 上次退出项目 ── */}
        <div className="card">
          <div className="spread">
            <b>上次退出的项目</b>
            {lastProject && lastProject.active_gate && (
              <span className="tag amber" style={{ fontSize: 11 }}>等待 Gate 决策</span>
            )}
          </div>
          {lastLoading ? (
            <div className="empty" style={{ padding: '24px 0', textAlign: 'center' }}>加载中...</div>
          ) : lastError ? (
            <div className="empty" style={{ padding: '24px 0', textAlign: 'center', color: 'var(--red)' }}>
              {lastError}
            </div>
          ) : lastProject ? (
            <div style={{ marginTop: 8 }}>
              <div className="ttl" style={{ fontSize: 14 }}>{lastProject.name} {statusBadge(lastProject)}</div>
              <div style={{ marginTop: 8 }}>
                <div className="kv"><span className="k">当前阶段</span><span className="v">{lastProject.current_stage ? STAGE_LABELS[lastProject.current_stage] : '未开始'}</span></div>
                <div className="kv"><span className="k">Evidence 缺口</span><span className="v">{lastProject.evidence_gap_count}</span></div>
                <div className="kv"><span className="k">源类型</span><span className="v">{lastProject.source_type}</span></div>
                <div className="kv"><span className="k">Gate</span><span className="v">{lastProject.active_gate ? '等待决策' : '无'}</span></div>
              </div>
              <button className="btn" style={{ width: '100%', justifyContent: 'center', marginTop: 10 }} onClick={() => openWs(lastProject.project_id)}>
                ▶ 继续此项目
              </button>
            </div>
          ) : (
            <div className="empty" style={{ padding: '24px 0', textAlign: 'center' }}>
              暂无，<a href="#" onClick={(e) => { e.preventDefault(); nav('/projects'); }}>前往项目列表</a>点击进入工作区后此处将显示最近一次访问的项目
            </div>
          )}
        </div>

        {/* ── 新建项目 ── */}
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
          <button className="btn" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }} onClick={() => nav('/projects/new')}>
            ＋ 新建迁移项目
          </button>
        </div>
      </div>

      {/* ═══ Row 2: 最近项目 (left) | 最近知识 (right) ═══ */}
      <div className="cardgrid two">
        {/* ── 最近项目 ── */}
        <div className="card">
          <div className="spread">
            <b>最近项目</b>
            <button className="btn sm ghost" onClick={() => nav('/projects')}>查看全部</button>
          </div>
          <div style={{ marginTop: 4 }}>
            {recentLoading ? (
              <div className="empty" style={{ padding: '12px 0' }}>加载中...</div>
            ) : recentError ? (
              <div className="empty" style={{ padding: '12px 0', color: 'var(--red)' }}>{recentError}</div>
            ) : recentProjects.length > 0 ? (
              recentProjects.map((p) => (
                <div key={p.project_id} className="listrow">
                  <div>
                    <div className="ttl">
                      {p.name} {statusBadge(p)}
                      {/* Gate/Risk 未真实化（R8+），概览不展示未真实指标（验收 §5.2-10） */}
                    </div>
                    <div className="meta">
                      {p.description || '暂无描述'} · {p.current_stage ? STAGE_LABELS[p.current_stage] : '未开始'}
                    </div>
                  </div>
                  <button className="btn sm" onClick={() => openWs(p.project_id)}>打开工作区</button>
                </div>
              ))
            ) : (
              <div className="empty" style={{ padding: '12px 0' }}>
                暂无项目，<a href="#" onClick={(e) => { e.preventDefault(); nav('/projects'); }}>前往创建</a>
              </div>
            )}
          </div>
        </div>

        {/* ── 最近知识 ── */}
        <div className="card">
          <div className="spread">
            <b>最近知识</b>
            <button className="btn sm ghost" onClick={() => nav('/knowledge')}>知识库</button>
          </div>
          <div style={{ marginTop: 4 }}>
            {knowledgeLoading ? (
              <div className="empty" style={{ padding: '12px 0' }}>加载中...</div>
            ) : knowledgeError ? (
              <div className="empty" style={{ padding: '12px 0', color: 'var(--red)' }}>{knowledgeError}</div>
            ) : knowledgeItems.length > 0 ? (
              knowledgeItems.map((k, i) => (
                <div key={i} className="listrow">
                  <div className="row" style={{ flexWrap: 'nowrap' }}>
                    <div className="ic">📄</div>
                    <div>
                      <div className="ttl">{k.name}</div>
                      <div className="meta">{k.desc}</div>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="listrow">
                <div className="meta">
                  知识库为空，<a href="#" onClick={(e) => { e.preventDefault(); nav('/knowledge'); }}>前往上传</a>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ═══ Row 3: 社区 / 文档 / 知识（保持不变） ═══ */}
      <div className="cardgrid three">
        {links.map((l) => (
          <div
            key={l.to}
            className="card statcard"
            onClick={() => (l.ext ? window.open(l.to, '_blank') : nav(l.to))}
          >
            <div className="ttl" style={{ fontSize: 14 }}>
              <span className="ic" style={{ color: 'var(--ink-2)', display: 'inline-flex' }}>
                <Icon name={l.ic} size={18} />
              </span>
              {l.title}
              {l.ext && (
                <span style={{ color: 'var(--ink-3)', fontWeight: 400, display: 'inline-flex' }}>
                  <Icon name="externalLink" size={13} />
                </span>
              )}
            </div>
            <div className="meta" style={{ marginTop: 6 }}>{l.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
