import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import { Markdown } from "../components/Markdown";
import { useSettled } from "../useSettled";
import { usePortalPath } from "./base";

/** Help articles: search, or the latest ones. */
export function HelpList() {
  const p = usePortalPath();
  const [q, setQ] = useState("");
  const search = useSettled(q);
  const list = useQuery({ queryKey: ["kb", "portal", search], queryFn: () => api.kb.list({ q: search || undefined, limit: 50 }) });
  return (
    <div className="stack" style={{ maxWidth: 760 }}>
      <WorkspaceTitle title="Help articles" area="FIND AN ANSWER" description="Search practical guidance from your service team." />
      <input type="search" aria-label="Search help articles" placeholder="What do you need help with?" value={q} onChange={(e) => setQ(e.target.value)} />
      <nav className="kb-list" aria-label="Help articles">
        {list.data?.length === 0 && (
          <p className="muted">
            {search ? "Nothing matches that. " : "No help articles yet. "}
            <Link to={p("/")}>Ask the service desk</Link>
          </p>
        )}
        {list.data?.map((a) => (
          <Link key={a.id} className="kb-item" to={p(`/help/${a.id}`)}>
            <strong>{a.title}</strong>
            {a.excerpt && <span className="small">{a.excerpt}</span>}
          </Link>
        ))}
      </nav>
    </div>
  );
}

export function HelpArticle() {
  const p = usePortalPath();
  const id = Number(useParams().articleId);
  const article = useQuery({ queryKey: ["kb", "portal", id], queryFn: () => api.kb.get(id) });
  const feedback = useMutation({ mutationFn: (helpful: boolean) => api.kb.feedback(id, helpful) });
  if (article.error) return <p className="error">{article.error.message}</p>;
  const a = article.data;
  if (!a) return null;
  return (
    <article className="card stack portal-help-article" style={{ maxWidth: 760, gap: 12 }} aria-label={a.title}>
      <header className="module-form-head"><p className="workspace-area">HELP ARTICLE</p>
        <Link className="small" to={p("/help")}>
          ← Help articles
        </Link>
        <h1 style={{ margin: "6px 0 0" }}>{a.title}</h1>
      </header>
      <Markdown text={a.body} />
      <div className="kb-suggest" role="group" aria-label="Was this helpful?">
        {feedback.isSuccess ? (
          <span>
            Thanks for telling us.{" "}
            {feedback.variables === false && (
              <>
                <Link to={p("/")}>Ask the service desk</Link> and we'll help.
              </>
            )}
          </span>
        ) : (
          <div className="row-gap">
            <span>Did this help?</span>
            <button className="btn2" type="button" onClick={() => feedback.mutate(true)}>
              Yes
            </button>
            <button className="btn2" type="button" onClick={() => feedback.mutate(false)}>
              No
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

/** Articles matching what's being typed into a request, before it's sent. */
export function HelpSuggestions({ text, category }: { text: string; category?: string }) {
  const p = usePortalPath();
  const search = useSettled(text.trim(), 400);
  const enough = search.length >= 4;
  const list = useQuery({
    queryKey: ["kb", "suggest", search, category ?? ""],
    queryFn: () => api.kb.list({ q: search, category: category || undefined, limit: 3 }),
    enabled: enough,
  });
  if (!enough || !list.data?.length) return null;
  return (
    <div className="kb-suggest" role="region" aria-label="These might help">
      <span className="small">
        <strong>These might help</strong> (they open in a new tab, so your request stays here)
      </span>
      {list.data.map((a) => (
        <a key={a.id} href={p(`/help/${a.id}`)} target="_blank" rel="noreferrer">
          {a.title}
        </a>
      ))}
    </div>
  );
}
