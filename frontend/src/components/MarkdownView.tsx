// Safe Markdown rendering (R15-4-C6/C7). React-markdown + GFM + sanitize (XSS-safe).
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

export function MarkdownView({ src }: { src: string }) {
  return (
    <div className="md-wrap">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {src || "_（暂无内容）_"}
      </ReactMarkdown>
    </div>
  );
}
