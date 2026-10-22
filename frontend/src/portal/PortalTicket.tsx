import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { api, type User } from "../api/client";
import { AttachedLine, AttachmentsSection } from "../components/Attachments";
import { StatusChip } from "../components/Chips";
import { useDropFiles } from "../components/useDropFiles";
import { fileSize, when } from "../format";
import { usePortalPath } from "./base";
import { TicketSurvey } from "./Survey";

export function PortalTicket({ me }: { me: User }) {
  const p = usePortalPath();
  const id = Number(useParams().ticketId);
  const queryClient = useQueryClient();
  const key = ["portal", "ticket", id];
  const ticket = useQuery({ queryKey: key, queryFn: () => api.portal.ticket(id), refetchInterval: 60_000 });
  const comments = useQuery({ queryKey: [...key, "comments"], queryFn: () => api.comments(id) });
  const files = useQuery({ queryKey: [...key, "files"], queryFn: () => api.attachments(id) });

  const [body, setBody] = useState("");
  const [attach, setAttach] = useState<File[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const drop = useDropFiles((files) => setAttach((a) => [...a, ...files]));

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["portal", "tickets"] });
  };
  const reply = useMutation({
    mutationFn: async () => {
      const c = await api.addComment(id, body, false);
      for (const f of attach) await api.upload(id, f, c.id);
    },
    onSuccess: () => {
      setBody("");
      setAttach([]);
    },
    onSettled: refresh,
  });
  const resolve = useMutation({ mutationFn: () => api.portal.resolve(id), onSettled: refresh });
  const dropped = useMutation({
    mutationFn: async (list: File[]) => {
      for (const f of list) await api.upload(id, f);
    },
    onSettled: refresh,
  });

  if (ticket.error) {
    return (
      <p className="error">
        {ticket.error.message}. <Link to={p("/tickets")}>Back to my tickets</Link>
      </p>
    );
  }
  if (!ticket.data) return null;
  const t = ticket.data;
  const done = t.status === "resolved" || t.status === "closed";
  const filesFor = (commentId: number | null) => (files.data ?? []).filter((f) => f.comment_id === commentId);

  return (
    <section className="stack" style={{ maxWidth: 820 }}>
      <div>
        <Link to={p("/tickets")}>My tickets</Link> / {t.label}
        <div className="row-gap" style={{ marginTop: 8 }}>
          <h1 className="spacer">{t.subject}</h1>
          <StatusChip status={t.status} />
        </div>
        <p className="muted small">
          {t.queue_name}
          {t.category ? ` · ${t.category}` : ""} · opened {when(t.created_at)}
          {t.assignee_name ? ` · ${t.assignee_name} is on it` : ""}
        </p>
      </div>

      {done && <TicketSurvey ticketId={id} />}

      <div className="card">
        <div className="lbl">You wrote, {when(t.created_at)}</div>
        <p className="body-text">{t.description || <span className="muted">No details given.</span>}</p>
        <AttachedLine files={filesFor(null)} />
      </div>
      {comments.data?.map((c) => (
        <div key={c.id} className="card">
          <div className="lbl">
            {c.author.id === me.id ? "You" : c.author.display_name}, {when(c.created_at)}
          </div>
          <p className="body-text">{c.body}</p>
          <AttachedLine files={filesFor(c.id)} />
        </div>
      ))}

      <AttachmentsSection
        files={files.data ?? []}
        comments={comments.data ?? []}
        describeAuthor={(uid, fallback) => (uid === me.id ? "you" : (fallback ?? "Service desk"))}
        onDropFiles={t.status === "closed" ? undefined : (list) => dropped.mutate(list)}
      />

      {t.status === "closed" ? (
        <div className="card">
          This ticket is closed. <Link to={p("/")}>Make a new request</Link> if you still need help, and mention {t.label}.
        </div>
      ) : (
        <div className={`card composer${drop.dragging ? " dropping" : ""}`} {...drop.props}>
          <label className="lbl" htmlFor="reply">
            {done ? "Still not working? Reply to reopen" : "Add a reply"}
          </label>
          <textarea id="reply" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
          {attach.length > 0 && (
            <ul className="files" style={{ listStyle: "none", padding: 0 }}>
              {attach.map((f, i) => (
                <li key={`${f.name}-${i}`} className="chip n">
                  {f.name} {fileSize(f.size)}
                </li>
              ))}
            </ul>
          )}
          {drop.dragging && <div className="drop-hint">Drop files to attach them</div>}
          {(reply.error || resolve.error) && (
            <p className="error" role="alert">
              {(reply.error ?? resolve.error)!.message}
            </p>
          )}
          <div className="row-gap" style={{ marginTop: 10 }}>
            <input
              ref={picker}
              type="file"
              multiple
              hidden
              data-testid="file-input"
              onChange={(e) => {
                setAttach([...attach, ...Array.from(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
            <button type="button" className="btn2" onClick={() => picker.current?.click()}>
              Attach file
            </button>
            <span className="muted small">or drag files here</span>
            <span className="spacer" />
            {!done && (
              <button type="button" className="btn2" disabled={resolve.isPending} onClick={() => resolve.mutate()}>
                This is fixed
              </button>
            )}
            <button type="button" className="btn" disabled={!body.trim() || reply.isPending} onClick={() => reply.mutate()}>
              Send reply
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
