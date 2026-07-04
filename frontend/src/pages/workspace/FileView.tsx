/** File view/edit component — real content preview + editing for writable files.
 *  UX-2: restores the 预览/编辑 mode toggle (removed during 真实化). Preview renders
 *  markdown for .md files and shows raw text otherwise; edit shows a textarea for
 *  writable files only. Save is available only in edit mode. */
import { useState, useEffect } from 'react';
import { fetchFileContent, saveFile, type FileContent } from '../../services/workspaceService';

interface Props {
  projectId: string;
  filePath: string;
  onClose: () => void;
}

type ViewMode = 'preview' | 'edit';

// Minimal markdown renderer for the preview pane (headings / lists / bold / code fences).
function renderMarkdown(md: string) {
  return md.split('\n').map((line, i) => {
    if (line.startsWith('### ')) return <h4 key={i} style={{ fontSize: 13, margin: '8px 0 4px' }}>{line.slice(4)}</h4>;
    if (line.startsWith('## ')) return <h3 key={i} style={{ fontSize: 15, margin: '10px 0 4px' }}>{line.slice(3)}</h3>;
    if (line.startsWith('# ')) return <h2 key={i} style={{ fontSize: 17, margin: '12px 0 6px' }}>{line.slice(2)}</h2>;
    if (line.startsWith('- ') || line.startsWith('* ')) return <li key={i} style={{ fontSize: 13, marginLeft: 18, lineHeight: 1.6 }}>{line.slice(2)}</li>;
    if (/^\d+\.\s/.test(line)) return <li key={i} style={{ fontSize: 13, marginLeft: 18, lineHeight: 1.6, listStyle: 'decimal' }}>{line.replace(/^\d+\.\s/, '')}</li>;
    return <div key={i} style={{ fontSize: 13, lineHeight: 1.7 }}>{line || ' '}</div>;
  });
}

export function FileView({ projectId, filePath, onClose }: Props) {
  const [data, setData] = useState<FileContent | null>(null);
  const [content, setContent] = useState('');
  const [saved, setSaved] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState<ViewMode>('preview');

  useEffect(() => {
    setLoading(true);
    fetchFileContent(projectId, filePath)
      .then(fc => { setData(fc); setContent(fc.content); setSaved(true); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [projectId, filePath]);

  const save = async () => {
    if (!data) return;
    setSaving(true);
    try {
      await saveFile(projectId, filePath, content);
      setSaved(true);
      setError(null);
    } catch (e: any) {
      setError(e.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div style={{ padding: 12, fontSize: 13 }}>加载中…</div>;
  if (error && !data) return <div style={{ padding: 12, fontSize: 13, color: 'var(--red)' }}>加载失败：{error}</div>;
  if (!data) return null;

  const isSourceFile = data.readonly;
  const canEdit = data.editable;
  const isMarkdown = /\.md$/i.test(filePath);
  const effectiveMode: ViewMode = canEdit ? mode : 'preview';  // read-only files never enter edit mode

  const tabBtn = (m: ViewMode, label: string, disabled = false) => (
    <button
      className="btn sm ghost"
      disabled={disabled}
      onClick={() => setMode(m)}
      style={{
        fontSize: 12, padding: '2px 10px',
        background: effectiveMode === m ? 'var(--color-primary-soft)' : 'transparent',
        color: disabled ? 'var(--color-text-muted)' : effectiveMode === m ? 'var(--color-primary)' : 'var(--color-text-muted)',
        fontWeight: effectiveMode === m ? 600 : 400,
      }}
    >{label}</button>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexShrink: 0 }}>
        <span className="mono" style={{ fontSize: 12 }}>
          {data.path}
          {isSourceFile && <span className="tag" style={{ marginLeft: 6, background: 'var(--amber)' }}>只读 — 源码写入归执行阶段</span>}
          {canEdit && <span className="tag" style={{ marginLeft: 6, background: 'var(--green)' }}>可编辑</span>}
          {!canEdit && !isSourceFile && <span className="tag" style={{ marginLeft: 6 }}>只读</span>}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {/* UX-2: 预览/编辑 mode toggle */}
          <div style={{ display: 'flex', gap: 2, border: '1px solid var(--color-border)', borderRadius: 6, padding: 1 }}>
            {tabBtn('preview', '预览')}
            {tabBtn('edit', '编辑', !canEdit)}
          </div>
          {effectiveMode === 'edit' && canEdit && (
            <button className="btn sm" onClick={save} disabled={saved || saving} style={{ fontSize: 12 }}>
              {saving ? '保存中…' : saved ? '已保存' : '保存'}
            </button>
          )}
          <button className="btn sm ghost" onClick={onClose} style={{ fontSize: 12 }}>关闭</button>
        </div>
      </div>

      {error && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 4 }}>{error}</div>}

      {/* Content */}
      {effectiveMode === 'edit' && canEdit ? (
        <textarea
          style={{
            flex: 1, width: '100%', fontFamily: 'var(--mono)', fontSize: 13,
            border: '1px solid var(--color-border)', borderRadius: 4, padding: 8,
            resize: 'none', background: 'var(--color-surface)', color: 'var(--color-text)',
          }}
          value={content}
          onChange={e => { setContent(e.target.value); setSaved(false); }}
        />
      ) : isMarkdown ? (
        <div
          style={{
            flex: 1, overflow: 'auto', background: 'var(--color-surface-subtle)',
            padding: 12, borderRadius: 4, border: '1px solid var(--color-border)',
          }}
        >
          {renderMarkdown(content)}
        </div>
      ) : (
        <pre
          style={{
            flex: 1, overflow: 'auto', fontFamily: 'var(--mono)', fontSize: 13,
            background: 'var(--color-surface-subtle)', padding: 8, margin: 0,
            borderRadius: 4, border: '1px solid var(--color-border)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}
        >
          {content}
        </pre>
      )}
      <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 4, flexShrink: 0 }}>
        {data.bytes} 字节{data.readonly_reason ? ` · ${data.readonly_reason}` : ''}
        {canEdit && !saved && <span style={{ color: 'var(--amber-text, #8d6e00)', marginLeft: 8 }}>· 未保存</span>}
      </div>
    </div>
  );
}
