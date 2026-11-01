"use client";

import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { normalizeMathMarkdown } from "../lib/markdown";

export const MarkdownContent = memo(function MarkdownContent({ content }: { content: string }) {
  const normalized = useMemo(() => normalizeMathMarkdown(content), [content]);
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
});
