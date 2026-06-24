import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

interface Section { id: string; label: string; children?: { id: string; label: string }[]; }

const SECTIONS: Section[] = [
  {
    id: 'overview', label: '平台概览',
    children: [
      { id: 'what-is-rebuild', label: '什么是 rebuild' },
      { id: 'quickstart', label: '快速入门' },
      { id: 'p0-p6', label: 'P0-P6 产品流程' },
    ],
  },
  {
    id: 'migration', label: '迁移指南',
    children: [
      { id: 'mg-workflow', label: '迁移工作流' },
      { id: 'mg-evidence', label: 'Evidence 证据链' },
      { id: 'mg-gate', label: 'Gate 授权闸门' },
    ],
  },
  {
    id: 'workspace', label: '工作区',
    children: [
      { id: 'ws-layout', label: '七区布局说明' },
      { id: 'ws-agent', label: 'Agent 对话' },
      { id: 'ws-files', label: '文件与材料视图' },
    ],
  },
  { id: 'integration', label: '集成与 API' },
  { id: 'cli', label: 'CLI 工具（规划中）' },
];

function renderContent(sectionId: string, nav: ReturnType<typeof useNavigate>) {
  switch (sectionId) {
    case 'overview':
    case 'what-is-rebuild':
      return (
        <div>
          <h1>什么是 rebuild</h1>
          <p className="sub" style={{ marginTop: 8 }}>
            rebuild 是一个面向软件重构与迁移的软件重构平台，首期主要应用于信创迁移场景。
            在用户拥有源代码的前提下，帮助用户完成项目迁移、重构、验证与交付。
          </p>
          <p style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.7 }}>
            当前版本 V26.1.1，处于 R3 前端体验壳阶段。平台核心闭环：接入源代码 → 建立事实源 → 评估风险 →
            制定方案 → 受控执行 → 验证评审 → 交付归档。
          </p>
          <div className="cardgrid" style={{ marginTop: 16 }}>
            {[
              ['/projects', '项目', '创建与管理迁移项目'],
              ['/projects/proj-001/workspace', '工作区', '全屏 IDE 工作区（在新标签页打开）'],
              ['/projects/proj-001/stage/p0', 'P0-P6', '产品流程阶段页'],
            ].map(([to, title, desc]) => (
              <div key={to} className="card statcard" onClick={() => to.includes('workspace') ? window.open(to, '_blank') : nav(to)}>
                <b>{title}</b>
                <div className="hash" style={{ marginTop: 4 }}>{desc}</div>
              </div>
            ))}
          </div>
        </div>
      );
    case 'quickstart':
      return (
        <div>
          <h1>快速入门</h1>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              { step: '1', title: '创建项目', desc: '在「项目」页新建 Project，填写基本信息与来源配置' },
              { step: '2', title: '首次引导', desc: '进入工作区后完成环境/提交/模型/执行模式四项配置' },
              { step: '3', title: '启动 P0-P6', desc: '从 P0 接入开始，逐步推进至 P6 交付' },
              { step: '4', title: '处理 Gate', desc: '阶段晋级时在 Gate 横幅中确认或拒绝' },
              { step: '5', title: '查看 Evidence', desc: '在右侧检视或独立 Evidence 页验证证据链' },
            ].map(s => (
              <div key={s.step} className="card" style={{ display: 'flex', gap: 12, padding: '12px 16px' }}>
                <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--accent-ink)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, flexShrink: 0 }}>{s.step}</div>
                <div>
                  <h3 style={{ fontSize: 14 }}>{s.title}</h3>
                  <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{s.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    case 'p0-p6':
      return (
        <div>
          <h1>P0-P6 产品流程</h1>
          <p className="sub" style={{ marginTop: 8 }}>P0-P6 是 rebuild 面向用户项目的可裁剪产品流程。</p>
          <div style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.8 }}>
            <div><b>P0 接入</b> — 源码接入与 Workspace 初始化</div>
            <div><b>P1 建档</b> — 建立项目事实源与技术栈清单</div>
            <div><b>P2 评估</b> — 识别迁移风险与依赖兼容性</div>
            <div><b>P3 规划</b> — 制定迁移方案与执行计划</div>
            <div><b>P4 执行</b> — 受控执行迁移与代码改造</div>
            <div><b>P5 验证</b> — 基于可验证证据评审迁移结果</div>
            <div><b>P6 交付</b> — 归档产物与证据链，完成交付</div>
          </div>
          <div className="banner info" style={{ marginTop: 14 }}>P 阶段可裁剪（如仅评估：P0→P1→P2→P6）。被裁剪阶段不得伪装为 completed。P5 验证不得以"看起来完成"通过（D-066）。</div>
        </div>
      );
    case 'mg-workflow':
      return <div><h1>迁移工作流</h1><p className="sub" style={{ marginTop: 8 }}>从源码接入到交付归档的完整迁移工作流（详细内容后续补充）。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'mg-evidence':
      return <div><h1>Evidence 证据链</h1><p className="sub" style={{ marginTop: 8 }}>No Evidence / No Trace, No Trusted Result（D-066）。Evidence candidate 不自动成为 Evidence validated。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'mg-gate':
      return <div><h1>Gate 授权闸门</h1><p className="sub" style={{ marginTop: 8 }}>P 阶段晋级 Gate 必须用户授权（D-023）。L5 高风险动作强制用户 Gate（D-034）。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'ws-layout':
      return <div><h1>七区布局</h1><p className="sub" style={{ marginTop: 8 }}>ActivityBar → 左侧面板 → 顶部状态栏 → Gate 横幅 → 中央 Tab → 底部 Dock → 右侧检视。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'ws-agent':
      return <div><h1>Agent 对话</h1><p className="sub" style={{ marginTop: 8 }}>Agent 对话 Tab 常驻不可关闭（D-047）。高风险动作需人工 Gate。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'ws-files':
      return <div><h1>文件与材料视图</h1><p className="sub" style={{ marginTop: 8 }}>代码视图（源码/产出代码/Patch）+ 材料视图（6 类材料身份，D-048）。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'integration':
      return <div><h1>集成与 API</h1><p className="sub" style={{ marginTop: 8 }}>执行器接入、编程 Agent 接入、远程资源、Git、MCP 等集成能力（R4/R7/R8 施工）。</p><span className="tag placeholder-tag">占位</span></div>;
    case 'cli':
      return (
        <div>
          <h1>CLI 工具</h1>
          <span className="tag future-tag" style={{ marginLeft: 8 }}>规划中</span>
          <div className="card" style={{ marginTop: 12 }}>
            <pre style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--ink-2)', lineHeight: 1.8 }}>
              {`# 安装（规划）\nnpm install -g @rebuild/cli\n\n# 创建迁移项目\nrebuild init --source ./my-app\n\n# 运行迁移\nrebuild migrate --manifest ./PROJECT.md\n\n# 验证结果\nrebuild verify --output ./report.json`}
            </pre>
          </div>
        </div>
      );
    default:
      return <div className="empty"><h2>{sectionId}</h2><span className="tag placeholder-tag">占位</span></div>;
  }
}

export function DocsPage() {
  const [active, setActive] = useState('what-is-rebuild');
  const nav = useNavigate();

  return (
    <div style={{ display: 'flex', height: '100vh', gap: 0 }}>
      {/* Left sidebar */}
      <aside style={{ width: 220, minWidth: 220, borderRight: '1px solid var(--line)', background: 'var(--surface)', overflow: 'auto', padding: '12px 0', flexShrink: 0 }}>
        <div style={{ padding: '8px 14px', fontSize: 14, fontWeight: 700 }}>📖 文档</div>
        <nav style={{ padding: '4px 8px' }}>
          {SECTIONS.map(s => (
            <div key={s.id}>
              <button
                onClick={() => { setActive(s.id); }}
                style={{ width: '100%', textAlign: 'left', padding: '6px 10px', border: 'none', borderRadius: 6, background: active === s.id ? 'var(--blue-bg)' : 'transparent', color: active === s.id ? 'var(--accent-ink)' : 'var(--ink)', fontWeight: active === s.id ? 600 : 400, fontSize: 13, cursor: 'pointer' }}
              >{s.label}</button>
              {s.children && active.startsWith(s.id) && (
                <div style={{ marginLeft: 14, borderLeft: '1px solid var(--line)', paddingLeft: 8 }}>
                  {s.children.map(c => (
                    <button key={c.id}
                      onClick={() => setActive(c.id)}
                      style={{ width: '100%', textAlign: 'left', padding: '4px 8px', border: 'none', borderRadius: 4, background: 'transparent', color: active === c.id ? 'var(--accent-ink)' : 'var(--ink-2)', fontWeight: active === c.id ? 600 : 400, fontSize: 12, cursor: 'pointer' }}
                    >{c.label}</button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </nav>
        <div style={{ borderTop: '1px solid var(--line)', margin: '8px 14px', paddingTop: 8 }}>
          <div className="hash" style={{ fontSize: 11 }}>当前版本：V26.1.1</div>
          <div className="hash" style={{ fontSize: 11 }}>当前阶段：R3 前端体验壳</div>
          <span className="tag placeholder-tag" style={{ marginTop: 6 }}>占位</span>
        </div>
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, overflow: 'auto', padding: '24px 32px', maxWidth: 800 }}>
        {renderContent(active, nav)}
      </main>
    </div>
  );
}
