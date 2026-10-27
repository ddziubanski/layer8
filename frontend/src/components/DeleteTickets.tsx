// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { ActionDialog } from "./ActionDialog";

type Target = { id: number; label: string; subject: string };
export function DeleteTickets({ tickets, onComplete, disabled = false, label = "Delete selected…" }: {
  tickets: Target[]; onComplete: (done: number[], failed: number[]) => void; disabled?: boolean; label?: string;
}) {
  const [review, setReview] = useState<Target[] | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<{ done: number[]; failed: Record<number, string> } | null>(null);
  const expected = `DELETE ${review?.length ?? 0}`;
  async function remove() {
    if (!review || confirmation !== expected || lock.current || review.length > 100) return;
    lock.current = true; setBusy(true);
    const done: number[] = [], failed: Record<number, string> = {};
    let interrupted = false;
    try {
      for (const target of review) {
        if (interrupted) { failed[target.id] = "Not attempted after the previous request failed; refresh before retrying."; continue; }
        try { await api.deleteTicket(target.id); done.push(target.id); }
        catch (error) {
          failed[target.id] = error instanceof Error ? error.message : "Deletion was not confirmed; refresh before retrying.";
          interrupted = !(error instanceof ApiError) || error.status === 401 || error.status === 403 || error.status >= 500;
        }
        setProgress(done.length + Object.keys(failed).length);
      }
      setResult({ done, failed }); onComplete(done, Object.keys(failed).map(Number));
    } finally { lock.current = false; setBusy(false); }
  }
  return <>
    <button className="btn2 danger" type="button" disabled={disabled || !tickets.length} onClick={() => { setReview([...tickets]); setConfirmation(""); setResult(null); setProgress(0); }}>{label}</button>
    {review && <ActionDialog title={`Delete ${review.length} ticket${review.length === 1 ? "" : "s"}?`} busy={busy} onClose={() => setReview(null)}>
      {!result ? <>
        <p>This permanently deletes the selected tickets, replies, notes, history and files. Child tickets remain. Closing a ticket keeps its record. Unsaved edits are not saved by deletion.</p>
        <ul style={{ maxHeight: 180, overflow: "auto" }}>{review.map(t => <li key={t.id}><strong>{t.label}</strong> {t.subject}</li>)}</ul>
        {review.length > 100 ? <p className="error" role="alert">Select at most 100 tickets per deletion.</p> : <label className="field">Type {expected} to confirm<input autoFocus value={confirmation} disabled={busy} onChange={e => setConfirmation(e.target.value)} autoComplete="off" /></label>}
        {busy && <p role="status">Deleting {progress} of {review.length}…</p>}
        <div className="row-gap"><button className="btn2" type="button" disabled={busy} onClick={() => setReview(null)}>Cancel</button>
          <button className="btn danger" type="button" disabled={busy || confirmation !== expected || review.length > 100} onClick={() => void remove()}>Delete permanently</button></div>
      </> : <>
        <p role="status">{result.done.length} deleted. {Object.keys(result.failed).length} not confirmed or not attempted.</p>
        {!!Object.keys(result.failed).length && <div role="alert" className="error small">{Object.entries(result.failed).map(([id, why]) => <p key={id}>#{id}: {why}</p>)}Refresh and review the remaining selection before trying again.</div>}
        <button className="btn2" type="button" onClick={() => setReview(null)}>Done</button>
      </>}
    </ActionDialog>}
  </>;
}
