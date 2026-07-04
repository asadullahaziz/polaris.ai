"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Styled markdown so the agent's comp tables + "why this price" render cleanly.
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: (p) => <p className="my-2 leading-relaxed" {...p} />,
        ul: (p) => <ul className="my-2 list-disc pl-5" {...p} />,
        ol: (p) => <ol className="my-2 list-decimal pl-5" {...p} />,
        li: (p) => <li className="my-0.5" {...p} />,
        h1: (p) => <h1 className="mt-3 mb-1 text-lg font-semibold" {...p} />,
        h2: (p) => <h2 className="mt-3 mb-1 text-base font-semibold" {...p} />,
        h3: (p) => <h3 className="mt-3 mb-1 text-sm font-semibold" {...p} />,
        strong: (p) => <strong className="font-semibold" {...p} />,
        a: (p) => <a className="underline underline-offset-2" {...p} />,
        code: (p) => (
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]" {...p} />
        ),
        table: (p) => (
          <div className="my-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm" {...p} />
          </div>
        ),
        th: (p) => (
          <th className="border bg-muted px-2 py-1 text-left font-medium" {...p} />
        ),
        td: (p) => <td className="border px-2 py-1" {...p} />,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
