import { Outlet } from 'react-router-dom';
import { MainNav } from '../components/navigation/MainNav';
import { GlobalMockBanner } from '../components/ui/MockBanner';
import { PlatformAssistant } from '../components/platform-assistant/PlatformAssistant';

export function PlatformLayout() {
  return (
    <div className="app">
      <MainNav />
      <div className="main-col">
        <GlobalMockBanner />
        <div className="view"><Outlet /></div>
      </div>
      <PlatformAssistant />
    </div>
  );
}
