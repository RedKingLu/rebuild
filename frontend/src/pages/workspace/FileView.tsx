/** File view/edit component — real content preview + editing for writable files. */
import { useState, useEffect } from 'react';
import { fetchFileContent, saveFile, type FileContent } from '../../services/workspaceService';

interface Props {
  projectId: string;
  filePath: string;
  onClose: () => void;
}

export function FileView({ projectId, filePath, onClose }: Props) {
  const [data, setData] = useState<FileContent | null>(null);
  const [content, setContent] = useState('');
  const [saved, setSaved] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
        <div style={{ display: 'flex', gap: 6 }}>
          {canEdit && (
            <button
              className="btn sm"
              onClick={save}
              disabled={saved || saving}
              style={{ fontSize: 12 }}
            >
              {saving ? '保存中…' : saved ? '已保存' : '保存'}
            </button>
          )}
          <button className="btn sm ghost" onClick={onClose} style={{ fontSize: 12 }}>关闭</button>
        </div>
      </div>

      {error && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 4 }}>{error}</div>}

      {/* Content */}
      {canEdit ? (
        <textarea
          style={{
            flex: 1, width: '100%', fontFamily: 'var(--mono)', fontSize: 13,
            border: '1px solid var(--color-border)', borderRadius: 4, padding: 8,
            resize: 'none', background: 'var(--color-surface)', color: 'var(--color-text)',
          }}
          value={content}
          onChange={e => { setContent(e.target.value); setSaved(false); }}
        />
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
      </div>
    </div>
  );
}
