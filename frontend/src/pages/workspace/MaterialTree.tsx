/** Material view — real directory scanning for materials/artifacts/evidence/reports/runs/logs. */
import type { FileTreeEntry, FileTreeResponse } from '../../services/workspaceService';
import { Icon } from '../../components/ui/Icon';
import { useState } from 'react';

interface Props {
  tree: FileTreeResponse | null;
  loading: boolean;
  onOpenFile: (path: string) => void;
}

export function MaterialTree({ tree, loading, onOpenFile }: Props) {
  if (loading) return <div className="empty" style={{ fontSize: 12 }}>加载中…</div>;
  if (!tree) return <div className="empty" style={{ fontSize: 12 }}>未加载材料视图</div>;

  return (
    <div className="tree">
      <b style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>项目材料</b>
      {tree.roots.map(root => (
        <div key={root.key} style={{ marginBottom: 6 }}>
          <div className="root" style={{ fontSize: 12, fontWeight: 600, padding: '2px 0' }}>
            {root.label}
            {root.editable && <span style={{ fontSize: 9, color: 'var(--green)', marginLeft: 4 }}>可编辑</span>}
          </div>
          {root.children?.length ? (
            <MaterialNode nodes={root.children} onOpenFile={onOpenFile} />
          ) : (
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', paddingLeft: 10 }}>（空 — 尚未生成材料）</div>
          )}
        </div>
      ))}
    </div>
  );
}

function MaterialNode({ nodes, onOpenFile, depth = 0 }: { nodes: FileTreeEntry[]; onOpenFile: (path: string) => void; depth?: number }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  return nodes.map(n => {
    const isDir = n.type === 'dir';
    const c = collapsed[n.path] !== false;
    return (
      <div key={n.path}>
        <div style={{ paddingLeft: 10 + depth * 12, cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}
          onClick={() => {
            if (isDir) setCollapsed(p => ({ ...p, [n.path]: !c }));
            else onOpenFile(n.path);
          }}>
          <Icon name={isDir ? 'chevronRight' : 'files'} size={14} />
          {n.name}
          {n.bytes != null && <span style={{ fontSize: 9, color: 'var(--color-text-muted)' }}>{fmt(n.bytes)}</span>}
        </div>
        {isDir && !c && n.children && <MaterialNode nodes={n.children} onOpenFile={onOpenFile} depth={depth + 1} />}
      </div>
    );
  });
}

function fmt(b: number) { return b < 1024 ? `${b}B` : `${(b / 1024).toFixed(1)}KB`; }
