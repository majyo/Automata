import { memo } from "react";
import type { MouseEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { openUrl } from "@tauri-apps/plugin-opener";

type MarkdownContentProps = {
  text: string;
  className?: string;
};

export const MarkdownContent = memo(function MarkdownContent({ text, className }: MarkdownContentProps) {
  return (
    <div className={`markdown-body ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              onClick={(event) => handleLinkClick(event, href)}
            >
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

function handleLinkClick(event: MouseEvent<HTMLAnchorElement>, href: string | undefined) {
  if (!href) {
    return;
  }
  event.preventDefault();
  // Inside Tauri, open links in the system browser; fall back to a plain window otherwise.
  openUrl(href).catch(() => window.open(href, "_blank", "noreferrer,noopener"));
}
