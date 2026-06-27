/** File tree component — code view (source/artifacts/patches). */
import { useState } from 'react';
import { Icon } from '../../components/ui/Icon';
import type { FileTreeEntry, FileTreeResponse } from '../../services/workspaceService';

interface Props {
  tree: FileTreeResponse | null;
  loading: boolean;
  onOpenFile: (path: string) => void;
}

export function FileTree({ tree, loading, onOpenFile }: Props) {
  if (loading) return <div className="empty" style={{ fontSize: 12 }}>加载中…</div>;
  if (!tree) return <div className="empty" style={{ fontSize: 12 }}>未加载文件树</div>;

  return (
    <div className="tree">
      <b style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>工作区文件</b>
      {tree.roots.map(root => (
        <div key={root.key} style={{ marginBottom: 6 }}>
          <div className="root" style={{ fontSize: 12, fontWeight: 600, padding: '2px 0' }}>
            {root.label}
            {!root.editable && <span className="ro" style={{ fontSize: 10, color: 'var(--color-text-muted)', marginLeft: 6 }}>只读</span>}
          </div>
          {root.children?.length ? (
            <TreeNode nodes={root.children} onOpenFile={onOpenFile} />
          ) : (
            <div className="node" style={{ color: 'var(--color-text-muted)', fontSize: 11, paddingLeft: 10 }}>（空）</div>
          )}
        </div>
      ))}
    </div>
  );
}

function TreeNode({ nodes, onOpenFile, depth = 0 }: { nodes: FileTreeEntry[]; onOpenFile: (path: string) => void; depth?: number }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  return nodes.map(n => {
    const isDir = n.type === 'dir';
    const isCollapsed = collapsed[n.path] !== false; // default open
    return (
      <div key={n.path}>
        <div
          className={`node${isDir ? ' root' : ''}`}
          style={{ paddingLeft: 10 + depth * 12, cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}
          onClick={() => {
            if (isDir) {
              setCollapsed(prev => ({ ...prev, [n.path]: !isCollapsed }));
            } else {
              onOpenFile(n.path);
            }
          }}
        >
          <Icon name={isDir ? 'chevronRight' : 'files'} size={14} />
          {n.name}
          {!n.editable && !isDir && <span style={{ fontSize: 9, color: 'var(--color-text-muted)' }}>只读</span>}
          {n.bytes != null && <span style={{ fontSize: 9, color: 'var(--color-text-muted)' }}>{formatBytes(n.bytes)}</span>}
        </div>
        {isDir && !isCollapsed && n.children && (
          <TreeNode nodes={n.children} onOpenFile={onOpenFile} depth={depth + 1} />
        )}
      </div>
    );
  });
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b}B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`;
  return `${(b / (1024 * 1024)).toFixed(1)}MB`;
}
