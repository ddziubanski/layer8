// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../../api/client";
import { useDropFiles } from "../../components/useDropFiles";
import { fileSize } from "../../format";

/** Work notes and replies, with optional files. A note stays internal (technicians and admins)
 * unless it's shown to the requester in the portal, and maybe emailed to them too. */
export function useComposerDraft() {
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [toRequester, setToRequester] = useState(false);
  const [email, setEmail] = useState(true);
  return { body, setBody, files, setFiles, toRequester, setToRequester, email, setEmail };
}

export function Composer({ ticketId, onDone, draft }: { ticketId: number; onDone: () => void; draft?: ReturnType<typeof useComposerDraft> }) {
  const local = useComposerDraft();
  const { body, setBody, files, setFiles, toRequester, setToRequester, email, setEmail } = draft ?? local;
  const picker = useRef<HTMLInputElement>(null);
  const drop = useDropFiles((dropped) => setFiles((f) => [...f, ...dropped]));

  const send = useMutation({
    mutationFn: async () => {
      const comment = await api.addComment(ticketId, body, !toRequester, toRequester && email);
      // Upload one at a time so a failure says which file it was.
      for (const file of files) {
        try {
          await api.upload(ticketId, file, comment.id);
        } catch (e) {
          throw new Error(`Saved, but ${file.name} wasn't attached: ${(e as Error).message}`);
        }
      }
    },
    onSuccess: () => {
      setBody("");
      setFiles([]);
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

  return (
    <div className={`card composer${toRequester ? "" : " internal"}${drop.dragging ? " dropping" : ""}`} {...drop.props}>
      <label className="lbl" htmlFor="composer">
        Work notes and replies
      </label>
      <textarea
        id="composer"
        rows={4}
        placeholder={toRequester ? "Write a reply to the requester." : "Write a work note. Only technicians and admins see it."}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="row-gap" style={{ marginTop: 8 }}>
        <label className="inline small">
          <input type="checkbox" checked={toRequester} onChange={(e) => setToRequester(e.target.checked)} /> Show to the requester in the portal
        </label>
        <label className="inline small">
          <input type="checkbox" checked={toRequester && email} disabled={!toRequester} onChange={(e) => setEmail(e.target.checked)} /> Email it to them too
        </label>
        <span className="muted small">{toRequester ? "" : "Internal: technicians and admins only."}</span>
      </div>
      {files.length > 0 && (
        <ul className="files" aria-label="Files to attach" style={{ listStyle: "none", padding: 0 }}>
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="chip n">
              {f.name} {fileSize(f.size)}{" "}
              <button type="button" className="linkish" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
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
      <div className="row-gap" style={{ marginTop: 10 }}>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            setFiles([...files, ...Array.from(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        <button className="btn2" type="button" onClick={() => picker.current?.click()}>
          Attach file
        </button>
        <span className="muted small">or drag files here</span>
        {!!canned.data?.length && (
          <select aria-label="Insert a canned reply" value="" disabled={insert.isPending} onChange={(e) => e.target.value && insert.mutate(Number(e.target.value))} style={{ width: "auto" }}>
            <option value="">Canned reply…</option>
            {canned.data.map((r) => (
              <option key={r.id} value={r.id}>{r.personal ? r.title : `${r.title} (shared)`}</option>
            ))}
          </select>
        )}
        <span className="spacer" />
        <button className={toRequester ? "btn" : "btn2"} type="button" disabled={!body.trim() || send.isPending} onClick={() => send.mutate()}>
          {action}
        </button>
      </div>
    </div>
  );
}
