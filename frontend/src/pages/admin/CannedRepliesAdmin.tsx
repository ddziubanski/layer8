// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type CannedReply, type User } from "../../api/client";
import { can } from "../../permissions";

const PLACEHOLDERS = "{first_name}, {name}, {ticket}, {subject}, {technician}, {queue}, {portal}";

/** Settings > Canned replies: your own, and the shared ones (with the permission to manage them). */
export function CannedRepliesAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const replies = useQuery({ queryKey: ["canned-replies"], queryFn: () => api.canned.list() });
  const [editing, setEditing] = useState<CannedReply | "new" | null>(null);
  const remove = useMutation({ mutationFn: api.canned.remove, onSettled: () => queryClient.invalidateQueries({ queryKey: ["canned-replies"] }) });
  return (
    <>
      <div className="page-head row-gap">
        <h1 className="spacer">Canned replies</h1>
        <button className="btn" type="button" onClick={() => setEditing("new")}>New canned reply</button>
      </div>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          Saved text to insert from a ticket&apos;s composer (Canned reply…). Keep your own, or share them with every technician
          {can(me, "canned_replies_manage") ? "" : " (sharing needs the permission in Settings > Permissions)"}. {PLACEHOLDERS} are filled in from the ticket.
        </p>
        {editing && <ReplyForm me={me} reply={editing === "new" ? null : editing} onDone={() => setEditing(null)} />}
        {replies.error && <p className="error">{replies.error.message}</p>}
        {remove.error && <p className="error" role="alert">{remove.error.message}</p>}
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Title</th>
                <th>Whose</th>
                <th>Queue</th>
                <th>Inserts as</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {replies.data?.map((r) => (
                <tr key={r.id}>
                  <td>{r.title}</td>
                  <td>{r.personal ? "Yours" : "Shared"}</td>
                  <td>{r.queue_name ?? "Every queue"}</td>
                  <td>{r.to_requester ? "Reply to the requester" : "Internal note"}</td>
                  <td className="row-gap">
                    {r.may_edit && (
                      <>
                        <button className="linkish" type="button" onClick={() => setEditing(r)}>Edit</button>
                        <button className="linkish" type="button" disabled={remove.isPending} onClick={() => remove.mutate(r.id)}>Delete</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {replies.data?.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">No canned replies yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function ReplyForm({ me, reply, onDone }: { me: User; reply: CannedReply | null; onDone: () => void }) {
  const queryClient = useQueryClient();
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const [title, setTitle] = useState(reply?.title ?? "");
  const [body, setBody] = useState(reply?.body ?? "Hi {first_name},\n\n");
  const [toRequester, setToRequester] = useState(reply?.to_requester ?? true);
  const [queueId, setQueueId] = useState<number | null>(reply?.queue_id ?? null);
  const [personal, setPersonal] = useState(reply ? reply.personal : !can(me, "canned_replies_manage"));
  const save = useMutation({
    mutationFn: () => {
      const values = { title, body, to_requester: toRequester, queue_id: queueId, personal };
      return reply ? api.canned.update(reply.id, values) : api.canned.create(values);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["canned-replies"] });
      onDone();
    },
  });
  return (
    <form className="card stack" aria-label={reply ? `Edit ${reply.title}` : "New canned reply"} onSubmit={(e) => (e.preventDefault(), save.mutate())}>
      <label className="field">
        Title
        <input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <label className="field">
        Text
        <textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} required />
      </label>
      <span className="muted small">Filled in from the ticket: {PLACEHOLDERS}.</span>
      <div className="row-gap">
        <label className="field">
          Queue
          <select value={queueId ?? ""} onChange={(e) => setQueueId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Every queue</option>
            {queues.data?.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
          </select>
        </label>
        <label className="inline">
          <input type="checkbox" checked={toRequester} onChange={(e) => setToRequester(e.target.checked)} /> Inserts as a reply to the requester
        </label>
        <label className="inline">
          <input type="checkbox" checked={!personal} disabled={!can(me, "canned_replies_manage")} onChange={(e) => setPersonal(!e.target.checked)} /> Shared with every technician
        </label>
      </div>
      {save.error && <p className="error" role="alert">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending || !title.trim() || !body.trim()}>{reply ? "Save" : "Add canned reply"}</button>
        <button className="btn2" type="button" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}
