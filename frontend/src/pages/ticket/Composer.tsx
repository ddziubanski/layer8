// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../../api/client";
import { useDropFiles } from "../../components/useDropFiles";
import { fileSize } from "../../format";
import { TicketSaveHere, useTicketSaveTask } from "./TicketSave";

/** Work notes and replies, with optional files. A note stays internal (technicians and admins)
 * unless it's shown to the requester in the portal, and maybe emailed to them too. */
export function useComposerDraft() {
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [toRequester, setToRequester] = useState(false);
  const [email, setEmail] = useState(true);
  const [minutes, setMinutes] = useState("");
  return { body, setBody, files, setFiles, toRequester, setToRequester, email, setEmail, minutes, setMinutes };
}

export function Composer({ ticketId, onDone, draft }: { ticketId: number; onDone: () => void; draft?: ReturnType<typeof useComposerDraft> }) {
  const local = useComposerDraft();
  const { body, setBody, files, setFiles, toRequester, setToRequester, email, setEmail, minutes, setMinutes } = draft ?? local;
  const picker = useRef<HTMLInputElement>(null);
  const progress = useRef<{ commentId: number; timeDone: boolean; uploaded: Set<File> } | null>(null);
  const [partial, setPartial] = useState(false);
  const locked = useRef(false);
  const drop = useDropFiles((dropped) => { if (!locked.current) setFiles((f) => [...f, ...dropped]); });
  function validate() {
    if (!body.trim()) throw new Error("Write a work note or reply before saving it.");
    if (minutes && (!Number.isInteger(Number(minutes)) || Number(minutes) < 1 || Number(minutes) > 1440)) throw new Error("Time must be a whole number from 1 to 1,440 minutes.");
  }

  const send = useMutation({
    mutationFn: async () => {
      validate();
      if (!progress.current) {
        const comment = await api.addComment(ticketId, body, !toRequester, toRequester && email);
        progress.current = { commentId: comment.id, timeDone: false, uploaded: new Set() };
        setPartial(true);
      }
      const saved = progress.current;
      if (Number(minutes) > 0 && !saved.timeDone) {
        await api.time.log(ticketId, { minutes: Number(minutes), note: "", comment_id: saved.commentId });
        saved.timeDone = true;
      }
      // Upload one at a time so a failure says which file it was.
      for (const file of files) {
        if (saved.uploaded.has(file)) continue;
        try {
          await api.upload(ticketId, file, saved.commentId);
          saved.uploaded.add(file);
        } catch (e) {
          throw new Error(`Saved, but ${file.name} wasn't attached: ${(e as Error).message}`);
        }
      }
    },
    onSuccess: () => {
      progress.current = null;
      setPartial(false);
      setBody("");
      setFiles([]);
      setMinutes("");
      // Back to the safe default: the next note is internal.
      setToRequester(false);
      setEmail(true);
    },
    onSettled: onDone,
  });
  const action = !toRequester ? "Add internal note" : email ? "Send to requester" : "Post to portal";
  const canned = useQuery({ queryKey: ["canned-replies", ticketId], queryFn: () => api.canned.list(ticketId) });
  const insert = useMutation({
    mutationFn: (id: number) => api.canned.render(id, ticketId),
    onSuccess: (r) => {
      setBody(body.trim() ? `${body.replace(/\s+$/, "")}\n\n${r.body}` : r.body);
      if (r.to_requester) setToRequester(true);
    },
  });

  const saveLabel = partial ? "Remaining time/attachments (note already saved)" : !toRequester ? "Internal note" : email ? "Email requester" : "Portal reply";
  const savingTicket = useTicketSaveTask("composer", { dirty: !!(body.trim() || files.length || minutes), busy: send.isPending || insert.isPending,
    label: saveLabel,
    error: send.error?.message, order: 100, validate, save: () => send.mutateAsync(),
    discard: () => { progress.current = null; setPartial(false); setBody(""); setFiles([]); setMinutes(""); setToRequester(false); setEmail(true); send.reset(); } });
  const frozen = savingTicket || send.isPending || insert.isPending || partial;
  locked.current = frozen;
  return (
    <div data-unsaved-work={!!(body || files.length || minutes)} className={`card composer${toRequester ? "" : " internal"}${drop.dragging ? " dropping" : ""}`} {...drop.props}>
      <label className="lbl" htmlFor="composer">
        Work notes and replies
      </label>
      <textarea
        id="composer"
        rows={4}
        placeholder={toRequester ? "Write a reply to the requester." : "Write a work note. Only technicians and admins see it."}
        value={body}
        disabled={frozen}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="row-gap" style={{ marginTop: 8 }}>
        <label className="inline small">
          <input type="checkbox" checked={toRequester} disabled={frozen} onChange={(e) => setToRequester(e.target.checked)} /> Show to the requester in the portal
        </label>
        <label className="inline small">
          <input type="checkbox" checked={toRequester && email} disabled={!toRequester || frozen} onChange={(e) => setEmail(e.target.checked)} /> Email it to them too
        </label>
        <span className="muted small">{toRequester ? "" : "Internal: technicians and admins only."}</span>
      </div>
      {files.length > 0 && (
        <ul className="files" aria-label="Files to attach" style={{ listStyle: "none", padding: 0 }}>
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="chip n">
              {f.name} {fileSize(f.size)}{" "}
              <button type="button" className="linkish" disabled={savingTicket || send.isPending || !!progress.current?.uploaded.has(f)} aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {drop.dragging && <div className="drop-hint">Drop files to attach them</div>}
      {insert.error && <p className="error" role="alert">{insert.error.message}</p>}
      {send.error && (
        <p className="error" role="alert">
          {send.error.message}
        </p>
      )}
      {partial && !send.isPending && <p className="small muted">The note/reply is already saved. Retry will save remaining time and attachments without posting it again.</p>}
      <div className="row-gap" style={{ marginTop: 10 }}>
        <input
          ref={picker}
          type="file"
          multiple
          disabled={frozen}
          hidden
          data-testid="file-input"
          onChange={(e) => {
            setFiles([...files, ...Array.from(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        <button className="btn2" type="button" disabled={frozen} onClick={() => picker.current?.click()}>
          Attach file
        </button>
        <span className="muted small">or drag files here</span>
        {!!canned.data?.length && (
          <select aria-label="Insert a canned reply" value="" disabled={insert.isPending || frozen} onChange={(e) => e.target.value && insert.mutate(Number(e.target.value))} style={{ width: "auto" }}>
            <option value="">Canned reply…</option>
            {canned.data.map((r) => (
              <option key={r.id} value={r.id}>{r.personal ? r.title : `${r.title} (shared)`}</option>
            ))}
          </select>
        )}
        <span className="spacer" />
        <label className="inline small">
          Time (min)
          <input type="number" min={1} max={1440} value={minutes} disabled={frozen} onChange={(e) => setMinutes(e.target.value)} style={{ width: 72 }} aria-label="Time spent, minutes" />
        </label>
        <button className={toRequester ? "btn" : "btn2"} type="button" disabled={!body.trim() || send.isPending || savingTicket} onClick={() => send.mutate()}>
          {partial ? "Retry remaining items" : action}
        </button>
      </div>
      {/* Other cards have unsaved changes too: save them all from here, with this note last. */}
      <TicketSaveHere show={!!body.trim() && !partial} own={saveLabel} onlyWithOthers />
    </div>
  );
}
