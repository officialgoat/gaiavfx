import type { ReactNode } from "react";

// Deliberately small: text nodes only, no HTML, URLs, images or HTML parser.
function inline(text: string): ReactNode[] {
  return text
    .split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_)/g)
    .map((part, i) => {
      if (part.startsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
      if (part.startsWith("**"))
        return <strong key={i}>{part.slice(2, -2)}</strong>;
      if (/^[*_]/.test(part) && part.length > 2)
        return <em key={i}>{part.slice(1, -1)}</em>;
      return part;
    });
}
export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  for (let i = 0; i < lines.length;) {
    const start = i;
    if (!lines[i].trim()) {
      i++;
      continue;
    }
    if (lines[i].startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```"))
        code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push(
        <pre key={start}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
    } else if (/^\s*(?:[-*]|\d+\.)\s+/.test(lines[i])) {
      const ordered = /^\s*\d+\./.test(lines[i]);
      const pattern = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/;
      const items: ReactNode[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(<li key={i}>{inline(lines[i++].replace(pattern, ""))}</li>);
      }
      blocks.push(
        ordered ? <ol key={start}>{items}</ol> : <ul key={start}>{items}</ul>,
      );
    } else {
      const paragraph = [lines[i++]];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(```|\s*(?:[-*]|\d+\.)\s+)/.test(lines[i])
      )
        paragraph.push(lines[i++]);
      blocks.push(<p key={start}>{inline(paragraph.join("\n"))}</p>);
    }
  }
  return <div className="chat-markdown">{blocks}</div>;
}
