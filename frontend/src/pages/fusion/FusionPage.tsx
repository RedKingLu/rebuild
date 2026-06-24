export function FusionPage() {
  return (
    <div>
      <div className="spread"><h1>聚合</h1><button className="btn sm">＋ 新建</button></div>
      <p className="sub">Fusion / SuperModel 聚合模型配置。Fusion 是模型能力/策略，不作为特殊流程/阶段（D-035）。默认关闭，可手动触发。</p>
      <span className="tag placeholder-tag" style={{ marginBottom: 12 }}>占位</span>
      <div className="banner info" style={{ marginBottom: 16 }}>
        Fusion 不阻塞基础 Flow；失败不影响主流程；输出作为增强证据进入 Artifact / Trace；不直接执行代码；不替代人工 Gate。
      </div>
      <div className="cardgrid">
        <div className="card">
          <b>聚合模型管理</b>
          <span className="tag placeholder-tag" style={{ marginLeft: 8 }}>占位</span>
          <div className="empty" style={{ padding: '24px 0' }}>
            <p className="sub">Fusion Profile 列表（Panel / Judge / Synthesizer 配置）。R13 真实功能合入。</p>
          </div>
        </div>
        <div className="card">
          <b>配置</b>
          <span className="tag placeholder-tag" style={{ marginLeft: 8 }}>占位</span>
          <div className="empty" style={{ padding: '24px 0' }}>
            <p className="sub">聚合策略配置（Panel 多模型审议 / Judge 评判 / Synthesizer 综合）。R13 施工。</p>
          </div>
        </div>
      </div>
    </div>
  );
}
