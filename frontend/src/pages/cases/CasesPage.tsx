export function CasesPage() {
  return (
    <div>
      <div className="spread"><h1>案例</h1><button className="btn sm">＋ 新建</button></div>
      <p className="sub">Case Library：Candidate → Approved 需人审；Case 不具备执行权（非 Skill 非 Tool）。</p>
      <span className="tag placeholder-tag" style={{ marginBottom: 12 }}>占位</span>
      <div className="empty">
        <p className="sub">案例库将在后续阶段建设。Case 可检索、可引用、可比较，但不可执行。</p>
        <span className="tag placeholder-tag">占位</span>
      </div>
    </div>
  );
}
