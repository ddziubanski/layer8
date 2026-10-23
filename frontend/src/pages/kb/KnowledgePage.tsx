import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, type KbArticle, type KbArticleIn, type Me } from "../../api/client";
import { Markdown } from "../../components/Markdown";
import { useSettled } from "../../useSettled";
import { can } from "../../permissions";

const STATUS: Record<string, [string, string]> = { draft: ["Draft", "a"], published: ["Published", "g"], archived: ["Archived", "n"] };
const AUDIENCE: Record<string, string> = { everyone: "Everyone (in the portal)", staff: "Technicians only" };

export function KbStatusChip({ status }: { status: string }) {
  const [label, tone] = STATUS[status] ?? [status, "n"];
  return <span className={`chip ${tone}`}>{label}</span>;
}

/** The knowledge base for technicians: search and browse, read, write and publish articles. */
export function KnowledgePage({ me }: { me: Me }) {
  const { articleId, mode } = useParams();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const search = useSettled(q);
  const list = useQuery({ queryKey: ["kb", "list", search, status], queryFn: () => api.kb.list({ q: search || undefined, status: status || undefined, limit: 100 }) });
  const id = articleId && articleId !== "new" ? Number(articleId) : null;

  return (
    <>
      <div className="row-gap" style={{ marginBottom: 16 }}>
        <WorkspaceTitle title="Knowledge base" area="KNOWLEDGE" />
        <span className="spacer" />
        <Link className="btn" to="/kb/new">
          New article
        </Link>
      </div>
      <div className="kb-layout">
        <div className="stack" style={{ gap: 10 }}>
          <input type="search" aria-label="Search articles" placeholder="Search articles…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="views" role="group" aria-label="Show" style={{ margin: 0 }}>
            {[
              ["", "All"],
              ["published", "Published"],
              ["draft", "Drafts"],
              ["archived", "Archived"],
            ].map(([v, label]) => (
              <button key={v} type="button" aria-pressed={status === v} onClick={() => setStatus(v)}>
                {label}
              </button>
            ))}
          </div>
          <nav className="kb-list" aria-label="Articles">
            {list.data?.length === 0 && <p className="muted small">{search ? "Nothing matches." : "No articles yet."}</p>}
            {list.data?.map((a) => (
              <Link key={a.id} className="kb-item" to={`/kb/${a.id}`} aria-current={a.id === id ? "page" : undefined}>
                <strong>{a.title}</strong>
                <span className="muted small">
                  {a.status !== "published" && <KbStatusChip status={a.status} />} {a.audience === "staff" ? "Technicians only" : ""}
                  {a.category && ` · ${a.category}`}
                </span>
                {a.excerpt && <span className="small">{a.excerpt}</span>}
              </Link>
            ))}
          </nav>
        </div>
        <div>
          {articleId === "new" ? (
            <Editor key="new" canPublish={can(me, "kb_publish")} />
          ) : id !== null ? (
            mode === "edit" ? <EditArticle key={`${id}-edit`} id={id} canPublish={can(me, "kb_publish")} /> : <ArticleView key={id} id={id} me={me} />
          ) : (
            <div className="card muted">
              Pick an article, or write a new one. Requesters see published articles meant for everyone in the portal, and they're suggested as they type a request.
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ArticleView({ id, me }: { id: number; me: Me }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const article = useQuery({ queryKey: ["kb", id], queryFn: () => api.kb.get(id) });
  const remove = useMutation({
    mutationFn: () => api.kb.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["kb"] });
      navigate("/kb");
    },
  });
  if (article.error) return <p className="error">{article.error.message}</p>;
  const a = article.data;
  if (!a) return null;
  return (
    <article className="card stack knowledge-detail" style={{ gap: 12 }} aria-label={a.title}>
      <div className="row-gap" style={{ alignItems: "flex-start" }}>
        <div className="spacer">
          <h2 style={{ margin: 0 }}>{a.title}</h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            <KbStatusChip status={a.status} /> {AUDIENCE[a.audience]}
            {a.category && ` · ${a.category}`}
            {a.updated_by && ` · updated by ${a.updated_by.display_name}`} {new Date(a.updated_at).toLocaleDateString()}
            {a.source_ticket_id && (
              <>
                {" · from "}
                <Link to={`/tickets/${a.source_ticket_id}`}>ticket #{a.source_ticket_id}</Link>
              </>
            )}
          </p>
        </div>
        <Link className="btn2" to={`/kb/${a.id}/edit`}>
          Edit
        </Link>
      </div>
      <Markdown text={a.body} />
      <p className="muted small" style={{ margin: 0 }}>
        Read {a.views} time(s) · helpful: {a.helpful_yes} yes, {a.helpful_no} no
        {a.keywords && ` · also found by: ${a.keywords}`}
      </p>
      <History article={a} canRestore={a.status !== "published" || can(me, "kb_publish")} />
      {can(me, "kb_delete") && (
        <div>
          <button
            className="linkish small"
            type="button"
            onClick={() => {
              if (window.confirm(`Delete "${a.title}"? Archiving keeps it out of searches without deleting it.`)) remove.mutate();
            }}
          >
            Delete
          </button>
        </div>
      )}
    </article>
  );
}

/** Earlier versions of the text: who changed it and when, what changed, and bringing one back. */
function History({ article, canRestore }: { article: KbArticle; canRestore: boolean }) {
  const queryClient = useQueryClient();
  const [shown, setShown] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const versions = useQuery({ queryKey: ["kb", article.id, "versions", article.updated_at], queryFn: () => api.kb.versions(article.id), enabled: shown });
  const detail = useQuery({ queryKey: ["kb", article.id, "version", open], queryFn: () => api.kb.version(article.id, open!), enabled: open !== null });
  const restore = useMutation({
    mutationFn: (n: number) => api.kb.restore(article.id, n),
    onSuccess: () => {
      setOpen(null);
      queryClient.invalidateQueries({ queryKey: ["kb"] });
    },
  });
  if (!shown) {
    return (
      <div>
        <button className="linkish small" type="button" onClick={() => setShown(true)}>
          History…
        </button>
      </div>
    );
  }
  return (
    <section className="stack" style={{ gap: 8 }} aria-label="History">
      <div className="lbl" style={{ margin: 0 }}>
        History
      </div>
      <table className="grid">
        <tbody>
          {versions.data?.map((v) => (
            <tr key={v.number}>
              <td>
                {v.number}
                {v.is_current && <span className="chip g"> current</span>}
              </td>
              <td>{v.author?.display_name ?? "–"}</td>
              <td>{new Date(v.created_at).toLocaleString()}</td>
              <td>{v.note || "–"}</td>
              <td>
                <button className="linkish small" type="button" onClick={() => setOpen(open === v.number ? null : v.number)}>
                  {open === v.number ? "Hide" : "Show"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail.data && open !== null && (
        <div className="stack" style={{ gap: 6 }} aria-label={`Version ${open}`} role="region">
          <pre className="log-details code diff" style={{ margin: 0 }}>
            {detail.data.diff.split("\n").map((line, i) => (
              <span key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-del" : undefined}>
                {line}
                {"\n"}
              </span>
            ))}
          </pre>
          {canRestore && !detail.data.is_current && (
            <div>
              <button className="btn2" type="button" disabled={restore.isPending} onClick={() => restore.mutate(open)}>
                Bring version {open} back
              </button>
            </div>
          )}
          {restore.error && <p className="error">{restore.error.message}</p>}
        </div>
      )}
    </section>
  );
}

function EditArticle({ id, canPublish }: { id: number; canPublish: boolean }) {
  const article = useQuery({ queryKey: ["kb", id], queryFn: () => api.kb.get(id) });
  return article.data ? <Editor initial={article.data} canPublish={canPublish} /> : null;
}

/** canPublish: Settings > Permissions lets them publish (and change published articles); others write drafts. */
function Editor({ initial, canPublish }: { initial?: KbArticle; canPublish: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const queues = useQuery({ queryKey: ["portal-queues"], queryFn: api.portal.queues, staleTime: 60_000 });
  const categories = [...new Set((queues.data ?? []).flatMap((q) => q.categories))].sort();
  const [f, setF] = useState<KbArticleIn>(() => ({
    title: initial?.title ?? "", body: initial?.body ?? "", keywords: initial?.keywords ?? "", category: initial?.category ?? "",
    audience: initial?.audience ?? "everyone", status: initial?.status ?? "draft",
  }));
  const [preview, setPreview] = useState(false);
  const text = useRef<HTMLTextAreaElement>(null);
  // An uploaded image goes in where the cursor is, on its own line.
  const image = useMutation({
    mutationFn: (file: File) => api.kb.uploadImage(initial!.id, file),
    onSuccess: (img) =>
      setF((cur) => {
        const at = text.current?.selectionStart ?? cur.body.length;
        const before = cur.body.slice(0, at), after = cur.body.slice(at);
        return { ...cur, body: `${before}${before && !before.endsWith("\n") ? "\n" : ""}${img.markdown}\n${after}` };
      }),
  });
  const save = useMutation({
    mutationFn: (status: KbArticleIn["status"]) => {
      const body = { ...f, status, category: f.category || null };
      return initial ? api.kb.update(initial.id, body) : api.kb.create(body);
    },
    onSuccess: (a) => {
      queryClient.invalidateQueries({ queryKey: ["kb"] });
      navigate(`/kb/${a.id}`);
    },
  });
  const published = initial?.status === "published";
  const locked = published && !canPublish;

  return (
    <form
      className="card stack"
      style={{ gap: 10 }}
      aria-label={initial ? "Edit article" : "New article"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(f.status);
      }}
    >
      <header className="module-form-head"><p className="workspace-area">KNOWLEDGE EDITOR</p><h2>{initial ? "Edit article" : "New article"}</h2></header>
      <label className="field">
        Title
        <input required maxLength={300} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="What people would type, e.g. Outlook keeps asking for my password" />
      </label>
      <div className="row-gap">
        <label className="field spacer">
          Category <span className="muted small">(suggested for requests in it)</span>
          <input list="kb-categories" maxLength={100} value={f.category ?? ""} onChange={(e) => setF({ ...f, category: e.target.value })} />
          <datalist id="kb-categories">
            {categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>
        <label className="field spacer">
          Who can read it
          <select value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value as KbArticleIn["audience"] })}>
            <option value="everyone">{AUDIENCE.everyone}</option>
            <option value="staff">{AUDIENCE.staff}</option>
          </select>
        </label>
      </div>
      <label className="field">
        Other words for it <span className="muted small">(found by search like the title)</span>
        <input maxLength={500} value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} placeholder="e.g. vpn anyconnect remote access" />
      </label>
      <div className="row-gap">
        <span className="lbl" style={{ margin: 0 }}>
          Article
        </span>
        <span className="spacer" />
        <div className="views" role="group" aria-label="Write or preview" style={{ margin: 0 }}>
          <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)}>
            Write
          </button>
          <button type="button" aria-pressed={preview} onClick={() => setPreview(true)}>
            Preview
          </button>
        </div>
      </div>
      {preview ? (
        <div className="card" style={{ margin: 0 }}>
          <Markdown text={f.body || "Nothing written yet."} />
        </div>
      ) : (
        <label className="field">
          <span className="muted small">## a heading, - a list, 1. steps, **bold**, `code`, [a link](https://…), &gt; a note</span>
          <textarea ref={text} aria-label="Article text" rows={18} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
        </label>
      )}
      {!preview && (
        <div className="row-gap small">
          {initial ? (
            <label className="linkish" style={{ cursor: "pointer" }}>
              {image.isPending ? "Uploading…" : "Add an image…"}
              <input
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                hidden
                disabled={locked || image.isPending}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) image.mutate(file);
                  e.target.value = "";
                }}
              />
            </label>
          ) : (
            <span className="muted">Save the draft to add images.</span>
          )}
          {image.error && <span className="error">{image.error.message}</span>}
        </div>
      )}
      {save.error && (
        <p className="error" role="alert" style={{ margin: 0 }}>
          {save.error.message}
        </p>
      )}
      {locked && <p className="muted small" style={{ margin: 0 }}>This article is published: only those who may publish can change it (Settings &gt; Permissions).</p>}
      <div className="row-gap">
        {!published && canPublish && (
          <button className="btn" type="button" disabled={save.isPending} onClick={() => save.mutate("published")}>
            Publish
          </button>
        )}
        <button className={published ? "btn" : "btn2"} type="button" disabled={save.isPending || locked} onClick={() => save.mutate(published ? "published" : (f.status === "archived" ? "archived" : "draft"))}>
          {published ? "Save" : "Save draft"}
        </button>
        {published && canPublish && (
          <button className="btn2" type="button" disabled={save.isPending} onClick={() => save.mutate("draft")}>
            Unpublish (back to draft)
          </button>
        )}
        {initial && initial.status !== "archived" && !locked && (
          <button className="linkish small" type="button" disabled={save.isPending} onClick={() => save.mutate("archived")}>
            Archive
          </button>
        )}
        <span className="spacer" />
        <button className="linkish small" type="button" onClick={() => navigate(initial ? `/kb/${initial.id}` : "/kb")}>
          Cancel
        </button>
      </div>
    </form>
  );
}
