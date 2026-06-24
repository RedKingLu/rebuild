import { Component, type ReactNode, type ErrorInfo } from 'react';

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error.message, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 48, maxWidth: 640, margin: '48px auto', textAlign: 'center' }}>
          <h2 style={{ color: 'var(--red)', marginBottom: 12 }}>页面错误</h2>
          <pre style={{ background: 'var(--surface-3)', padding: 16, borderRadius: 8, color: 'var(--red)', fontSize: 13, overflow: 'auto', whiteSpace: 'pre-wrap', border: '1px solid var(--line)' }}>{this.state.error.message}</pre>
          <button className="btn" style={{ marginTop: 16 }} onClick={() => window.location.reload()}>刷新页面</button>
        </div>
      );
    }
    return this.props.children;
  }
}
