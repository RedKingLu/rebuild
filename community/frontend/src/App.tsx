import { NavLink, Route, Routes } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { ResourceDetailPage } from "./pages/ResourceDetailPage";
import { ModelsPage } from "./pages/ModelsPage";
import { EvaluationsPage } from "./pages/EvaluationsPage";

const NAV = [
  { to: "/", label: "首页" },
  { to: "/resources", label: "资源" },
  { to: "/models", label: "模型" },
  { to: "/evaluations", label: "评测" },
];

export function App() {
  return (
    <div className="comm-shell">
      <header className="comm-header">
        <div className="comm-brand">rebuild 社区</div>
        <nav className="comm-nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => "comm-navlink" + (isActive ? " active" : "")}>{n.label}</NavLink>
          ))}
        </nav>
        <span className="comm-badge local">本地社区 · R15</span>
      </header>
      <main className="comm-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/resources" element={<ResourcesPage />} />
          <Route path="/resources/:id" element={<ResourceDetailPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/evaluations" element={<EvaluationsPage />} />
        </Routes>
      </main>
      <footer className="comm-footer">
        <span>rebuild 社区 · 本地可运行骨架（R15）· 内容由发布侧 seed，非官方运营</span>
      </footer>
    </div>
  );
}
