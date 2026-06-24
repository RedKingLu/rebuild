import { useState } from 'react';

export function PlatformAssistant() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        style={{
          position: 'fixed', bottom: 24, right: 24, width: 48, height: 48, borderRadius: '50%',
          background: 'var(--accent-ink)', color: '#fff', border: 'none', fontSize: 22,
          boxShadow: '0 4px 16px rgba(0,0,0,.15)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
        title="平台助手"
      >
        ✦
      </button>
      {open && (
        <div style={{
          position: 'fixed', bottom: 84, right: 24, width: 380, height: 520, background: 'var(--surface)',
          border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 8px 32px rgba(0,0,0,.12)',
          zIndex: 1000, display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div className="spread" style={{ padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
            <b style={{ fontSize: 14 }}>平台助手</b>
            <div className="row">
              <span className="tag violet">Mock</span>
              <span className="tag grey">未接真实服务</span>
              <button className="btn sm ghost" onClick={() => setOpen(false)}>✕</button>
            </div>
          </div>
          <div style={{ flex: 1, padding: 16, overflow: 'auto' }}>
            <div className="msg" style={{ background: 'var(--surface-2)', padding: 10, borderRadius: 8, marginBottom: 10, fontSize: 13 }}>
              你好！我是平台助手（AI 精灵），可以帮助你了解如何使用 rebuild 平台。
            </div>
            <div className="msg" style={{ background: 'var(--surface-2)', padding: 10, borderRadius: 8, marginBottom: 10, fontSize: 13 }}>
              当前为 R3 体验壳阶段，所有能力标记为 [mock]。真实平台助手将在后续阶段接入 ModelGateway，支持对话答疑与模型连通性自测。
            </div>
          </div>
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--line)', display: 'flex', gap: 8 }}>
            <select style={{ flex: 1, fontSize: 12 }} disabled>
              <option>模型切换（未接真实服务）</option>
            </select>
            <button className="btn sm" disabled>发送</button>
          </div>
        </div>
      )}
    </>
  );
}
