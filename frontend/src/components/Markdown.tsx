import type { ReactNode } from "react";

/**
 * The small Markdown knowledge base articles are written in, made into React elements (never HTML,
 * so nothing typed can run): ## headings, paragraphs, - and 1. lists, ``` code blocks, > notes,
 * **bold**, *italic*, `code`, [links](https://...) and ![images](/api/kb/files/...). Only http(s) and
 * mailto links become links, and only images uploaded to an article are shown.
 */
export function Markdown({ text }: { text: string }) {
  return <div className="md">{blocks(text)}</div>;
}

function blocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(<pre key={out.length} className="code">{code.join("\n")}</pre>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(h[1].length + 1, 5);
      const Tag = `h${level}` as "h2" | "h3" | "h4" | "h5";
      out.push(<Tag key={out.length}>{inline(h[2])}</Tag>);
      i++;
      continue;
    }
    const list = /^\s*([-*+]|\d+[.)])\s+/;
    if (list.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && list.test(lines[i])) {
        let item = lines[i++].replace(list, "");
        // Indented lines carry on the item above.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !list.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      const Tag = ordered ? "ol" : "ul";
      out.push(
        <Tag key={out.length}>
          {items.map((it, n) => (
            <li key={n}>{inline(it)}</li>
          ))}
        </Tag>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      out.push(<blockquote key={out.length}>{inline(quote.join(" "))}</blockquote>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(<p key={out.length}>{inline(para.join(" "))}</p>);
  }
  return out;
}

const SAFE_URL = /^(https?:\/\/|mailto:)/i;
const IMAGE = /^!\[([^\]]*)\]\((\/api\/kb\/files\/[0-9a-f-]+)\)$/;
const TOKEN = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(!?\[[^\]]*\]\([^)\s]+\))/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const key = out.length;
    if (m[1]) out.push(<code key={key}>{t.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={key}>{inline(t.slice(2, -2))}</strong>);
    else if (m[3]) out.push(<em key={key}>{inline(t.slice(1, -1))}</em>);
    else if (t.startsWith("!")) {
      const img = IMAGE.exec(t);
      if (img) out.push(<img key={key} className="md-img" src={img[2]} alt={img[1]} loading="lazy" />);
      else out.push(t.slice(1));
    } else {
      const [, label, url] = /^\[([^\]]*)\]\(([^)\s]+)\)$/.exec(t)!;
      out.push(
        SAFE_URL.test(url) ? (
          <a key={key} href={url} target="_blank" rel="noreferrer noopener">
            {label}
          </a>
        ) : (
          label
        ),
      );
    }
    last = m.index! + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
