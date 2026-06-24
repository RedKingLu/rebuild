const TABS = [
  ['execution', '执行器接入', 'ExecutionProvider 列表（Built-in / MCP / Wrapper）'],
  ['agent', '编程 Agent 接入', '外部编程 Agent（经 Agent Wrapper 统一为 ExecutionRequest/Result）'],
  ['remote', '远程资源', 'SSH / Docker / Remote Host（Environment Profile, D-051）'],
  ['git', 'Git', 'Git 仓库连接 + 当前更改 + 提交/推送（写操作需 Gate）'],
  ['other', '其他集成', '模型网关 / 文件系统 / 其他基础设施'],
] as const;

export function IntegrationsPage() {
  return (
    <div>
      <div className="spread">
        <h1>集成</h1>
        <button className="btn sm">＋ 添加</button>
      </div>
      <p className="sub">执行器、编程 Agent、远程资源、Git 与基础设施接入点。Git 提交/推送为写操作，需 Gate 通过（D-034）。</p>
      <span className="tag placeholder-tag" style={{ marginBottom: 12 }}>占位</span>
      <div className="statgrid">
        {TABS.map(([key, label]) => (
          <div key={key} className="card statcard">
            <b>{label}</b>
            <div className="snum">0<small> / 0</small></div>
            <div className="slabel">启用 / 总数</div>
            <span className="tag placeholder-tag" style={{ marginTop: 8 }}>占位</span>
          </div>
        ))}
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <b>集成详情</b>
        <div className="empty">
          <p className="sub">集成页将在 R4/R7/R8 阶段接入真实数据。当前为基础入口占位。</p>
          <span className="tag placeholder-tag">占位</span>
        </div>
      </div>
    </div>
  );
}
