import { createBrowserRouter } from 'react-router-dom';
import { PlatformLayout } from './layouts/PlatformLayout';
import { ProjectLayout } from './layouts/ProjectLayout';
import { WorkspaceLayout } from './layouts/WorkspaceLayout';
import { ErrorBoundary } from './components/ui/ErrorBoundary';

// Lazy-loaded page imports
import { OverviewPage } from './pages/dashboard/OverviewPage';
import { ProjectListPage } from './pages/projects/ProjectListPage';
import { ProjectCreatePage } from './pages/projects/ProjectCreatePage';
import { ProjectDetailPage } from './pages/projects/ProjectDetailPage';
import { WorkspacePage } from './pages/workspace/WorkspacePage';
import { ResourcesPage } from './pages/resources/ResourcesPage';
import { IntegrationsPage } from './pages/integrations/IntegrationsPage';
import { ModelsPage } from './pages/models/ModelsPage';
import { FusionPage } from './pages/fusion/FusionPage';
import { CasesPage } from './pages/cases/CasesPage';
import { KnowledgePage } from './pages/knowledge/KnowledgePage';
import { CommunityPage } from './pages/community/CommunityPage';
import { DocsPage } from './pages/docs/DocsPage';
import { SettingsPage } from './pages/settings/SettingsPage';
import { NotFoundPage } from './pages/errors/NotFoundPage';

import type { ReactNode } from 'react';
function wrap(el: ReactNode) { return <ErrorBoundary>{el}</ErrorBoundary>; }

export const router = createBrowserRouter([
  // Platform routes
  {
    element: <PlatformLayout />,
    children: [
      { path: '/', element: wrap(<OverviewPage />) },
      { path: '/projects', element: wrap(<ProjectListPage />) },
      { path: '/projects/new', element: wrap(<ProjectCreatePage />) },
      { path: '/resources', element: wrap(<ResourcesPage />) },
      { path: '/resources/:type', element: wrap(<ResourcesPage />) },
      { path: '/integrations', element: wrap(<IntegrationsPage />) },
      { path: '/models', element: wrap(<ModelsPage />) },
      { path: '/fusion', element: wrap(<FusionPage />) },
      { path: '/cases', element: wrap(<CasesPage />) },
      { path: '/knowledge', element: wrap(<KnowledgePage />) },
      { path: '/settings', element: wrap(<SettingsPage />) },
      { path: '/settings/security', element: wrap(<SettingsPage />) },
    ],
  },
  // Project routes — 仅创建与展示（D-046）。
  // Stage / Run / 产物 / 证据 / Trace / Audit / Gate / 设置 等深度交互全部在 Workspace 内进行。
  {
    element: <ProjectLayout />,
    children: [
      { path: '/projects/:id', element: wrap(<ProjectDetailPage />) },
    ],
  },
  // Standalone fullscreen routes (no main nav — opened in new tabs)
  {
    element: <WorkspaceLayout />,
    children: [
      { path: '/projects/:id/workspace', element: wrap(<WorkspacePage />) },
      { path: '/community', element: wrap(<CommunityPage />) },
      { path: '/docs', element: wrap(<DocsPage />) },
    ],
  },
  // 404
  { path: '*', element: wrap(<NotFoundPage />) },
]);
