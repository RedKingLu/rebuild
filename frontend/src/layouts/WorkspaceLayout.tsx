import { Outlet } from 'react-router-dom';

export function WorkspaceLayout() {
  return <div className="app ws-mode"><Outlet /></div>;
}
