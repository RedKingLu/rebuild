export function ModelsPage() {
  return (
    <div>
      <div className="spread"><h1>模型</h1><button className="btn sm">＋ 新建</button></div>
      <p className="sub">ModelGateway 抽象 + LiteLLM SDK Adapter（D-039, D-065）。密钥脱敏存储，前端不回显原值。</p>
      <span className="tag placeholder-tag" style={{ marginBottom: 12 }}>占位</span>
      <div className="statgrid">
        <div className="card statcard">
          <b>Provider</b>
          <div className="snum">0<small> / 0</small></div>
          <div className="slabel">可用 / 总数</div>
          <span className="tag grey">未接真实服务</span>
        </div>
        <div className="card statcard">
          <b>ModelProfile</b>
          <div className="snum">0<small> / 0</small></div>
          <div className="slabel">启用 / 总数</div>
          <span className="tag grey">未接真实服务</span>
        </div>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <b>模型管理</b>
        <div className="empty">
          <p className="sub">模型 Provider / Profile / Binding / Usage / Failover 管理将在 R5（模型网关与模型策略）阶段施工。</p>
          <span className="tag placeholder-tag">占位</span>
          <span className="tag not-connected-tag" style={{ marginLeft: 8 }}>未接真实服务</span>
        </div>
      </div>
    </div>
  );
}
