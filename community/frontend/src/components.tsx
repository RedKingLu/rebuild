// Shared community components (R15-4-C3 frontend).

import { api, type ResourceCard } from "./api";

export function SourceBadge({ source, verified }: { source: string; verified?: boolean }) {
  const cls = "badge badge-" + (source === "official" ? "official" : "community");
  return <span className={cls}>{verified ? "官方 ✓" : source === "official" ? "官方" : "社区"}</span>;
}

export function ResourceIcon({ iconUrl, name }: { iconUrl: string | null; name: string }) {
  if (iconUrl) return <img className="card-icon" src={iconUrl} alt={name} />;
  // Stable letter avatar (no emoji, per R15 style discipline).
  return <div className="card-icon fallback">{(name || "?").slice(0, 1).toUpperCase()}</div>;
}

export function DownloadButton({ id, disabled }: { id: string; disabled?: boolean }) {
  const href = api.downloadUrl(id);
  return (
    <a className="btn btn-sm" href={href} aria-disabled={disabled} onClick={(e) => disabled && e.preventDefault()}>
      下载
    </a>
  );
}

export function Card({ c }: { c: ResourceCard }) {
  return (
    <div className="card">
      <div className="card-head">
        <ResourceIcon iconUrl={c.icon_url} name={c.display_name || c.name} />
        <div className="card-title">
          <NavLink className="card-name" to={`/resources/${c.id}`}>{c.display_name || c.name}</NavLink>
          <div className="card-meta"><SourceBadge source={c.source} verified={c.verified} /> <span className="muted">v{c.version}</span> · <span className="muted">↓{c.download_count}</span></div>
        </div>
      </div>
      <p className="card-desc">{c.description}</p>
      <div className="card-tags">{(c.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}</div>
    </div>
  );
}

// Minimal markdown renderer (README in knowledge/resources). No sanitize lib on
// the community frontend by default; content here is release-side seed (trusted).
// If rendering untrusted markdown, plug in DOMPurify at this boundary.
export function Md({ src }: { src: string }) {
  // very light: paragraphs + headings + code fences + inline code + <table>
  const html = renderSimpleMd(src);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

function esc(s: string) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function renderSimpleMd(md: string): string {
  const lines = md.split("\n");
  let out = "";
  let inCode = false;
  let inTable = false;
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      out += inCode ? "</code></pre>" : "<pre><code>";
      inCode = !inCode;
      continue;
    }
    if (inCode) { out += esc(line) + "\n"; continue; }
    if (line.startsWith("|")) {
      const cells = line.split("|").filter(Boolean).map((c) => c.trim());
      if (cells.every((c) => /^[-:]+$/.test(c))) continue; // separator
      if (!inTable) { out += "<table><thead><tr>" + cells.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>"; inTable = true; }
      else out += "<tr>" + cells.map((c) => `<td>${esc(c)}</td>`).join("") + "</tr>";
      continue;
    } else if (inTable) { out += "</tbody></table>"; inTable = false; }
    if (line.startsWith("### ")) out += `<h3>${esc(line.slice(4))}</h3>`;
    else if (line.startsWith("## ")) out += `<h2>${esc(line.slice(3))}</h2>`;
    else if (line.startsWith("# ")) out += `<h1>${esc(line.slice(2))}</h1>`;
    else if (/^\s*[-*]\s+/.test(line)) out += `<li>${esc(line.replace(/^\s*[-*]\s+/, ""))}</li>`;
    else if (line.trim() === "") out += "";
    else out += `<p>${esc(line)}</p>`;
  }
  if (inTable) out += "</tbody></table>";
  if (inCode) out += "</code></pre>";
  return out;
}

import { NavLink } from "react-router-dom";

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
