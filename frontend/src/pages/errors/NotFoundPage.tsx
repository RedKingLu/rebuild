import { useNavigate } from 'react-router-dom';

export function NotFoundPage() {
  const nav = useNavigate();
  return (
    <div style={{ textAlign: 'center', padding: '80px 32px' }}>
      <h1 style={{ fontSize: 48, color: 'var(--ink-3)', marginBottom: 8 }}>404</h1>
      <h2>页面未找到</h2>
      <p className="sub">请求的路径不存在。请检查 URL 是否正确，或通过下方入口返回平台。</p>
      <div className="row" style={{ justifyContent: 'center', marginTop: 16 }}>
        <button className="btn" onClick={() => nav('/')}>返回平台概览</button>
        <button className="btn ghost" onClick={() => nav('/projects')}>返回项目列表</button>
      </div>
    </div>
  );
}
