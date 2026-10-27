// SPDX-License-Identifier: AGPL-3.0-or-later
import { createContext, useContext, useEffect, useRef, useState, type MutableRefObject, type ReactNode } from "react";

type Task = { dirty: boolean; busy: boolean; label: string; error?: string; validate?: () => void; save: () => Promise<unknown>; discard?: () => void; order?: number };
type Controller = { register: (id: string, task: MutableRefObject<Task>) => () => void; changed: () => void; busy: boolean; tasks: Task[]; dirtyIds: string[]; discard: () => void; save: () => Promise<void>; message: string; error: string };
const Context = createContext<Controller | null>(null);

/** Coordinates existing ticket actions; tool execution and destructive actions stay explicit. */
export function TicketSaveProvider({ children }: { children: ReactNode }) {
  const registry = useRef(new Map<string, MutableRefObject<Task>>());
  const locked = useRef(false);
  const [saving, setSaving] = useState(false);
  const [, update] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const changed = useRef(() => { update(n => n + 1); }).current;
  const register = useRef((id: string, task: MutableRefObject<Task>) => {
    registry.current.set(id, task); changed();
    return () => { registry.current.delete(id); changed(); };
  }).current;
  const tasks = [...registry.current.values()].map(ref => ref.current);
  const busy = saving || tasks.some(task => task.busy);
  const dirtyIds = [...registry.current.entries()].filter(([, ref]) => ref.current.dirty).map(([id]) => id);
  function discard() {
    if (locked.current || [...registry.current.values()].some(ref => ref.current.busy)) return;
    for (const ref of registry.current.values()) if (ref.current.dirty) ref.current.discard?.();
    setError(""); setMessage("Unsaved edits canceled. Saved changes are unchanged.");
  }
  async function save() {
    if (locked.current || [...registry.current.values()].some(ref => ref.current.busy)) return;
    const pending = [...registry.current.values()].filter(ref => ref.current.dirty).sort((a, b) => (a.current.order ?? 0) - (b.current.order ?? 0));
    if (!pending.length) return;
    locked.current = true; setSaving(true); setError(""); setMessage("");
    let completed = 0;
    try {
      // Check every required field before any write (especially before sending a reply).
      for (const ref of pending) ref.current.validate?.();
      for (const ref of pending) { await ref.current.save(); completed++; }
      setMessage("Ticket saved.");
    } catch (failure) {
      setError(`${completed ? "Some changes were saved. " : ""}${failure instanceof Error ? failure.message : "Could not save the ticket"} Remaining changes are kept; review and retry.`);
    } finally { locked.current = false; setSaving(false); }
  }
  return <Context.Provider value={{ register, changed, busy, tasks, dirtyIds, discard, save, message, error }}>{children}</Context.Provider>;
}

export function useTicketSaveState() { return useContext(Context); }

export function useTicketSaveTask(id: string, task: Task) {
  const context = useContext(Context);
  const current = useRef(task); current.current = task;
  const register = context?.register, changed = context?.changed;
  useEffect(() => register?.(id, current), [register, id]);
  useEffect(() => { changed?.(); }, [changed, task.dirty, task.busy, task.label, task.error]);
  return context?.busy ?? false;
}

export function TicketSaveButton() {
  const context = useContext(Context);
  if (!context) return null;
  const pending = context.tasks.filter(task => task.dirty);
  const failed = context.tasks.some(task => task.error);
  return <div className="stack" role="group" aria-label="Save the whole ticket" style={{ gap: 5, maxWidth: 340 }}>
    <button className="btn" type="button" disabled={context.busy || !pending.length} onClick={() => void context.save()}>{context.busy ? "Saving ticket…" : "Save ticket"}</button>
    {context.error ? <span role="alert" className="error small">{context.error}</span> : <span role="status" className="muted small">{pending.length ? `Pending: ${pending.map(task => task.label).join(" · ")}` : failed ? "Review the failed changes below." : context.message || "All ticket changes saved"}</span>}
  </div>;
}

/** The same Save ticket, right where the edit is, so nobody has to go back up to the header.
 * Shown only while this part has unsaved changes; it still saves everything pending, in order. */
export function TicketSaveHere({ show, own, onlyWithOthers }: { show: boolean; own?: string; onlyWithOthers?: boolean }) {
  const context = useContext(Context);
  if (!context || !show) return null;
  const others = context.tasks.filter(task => task.dirty && task.label !== own).map(task => task.label);
  if (onlyWithOthers && !others.length) return null;
  return <div className="row-gap ticket-save-here" role="group" aria-label="Save here">
    <button className="btn" type="button" disabled={context.busy} onClick={() => void context.save()}>{context.busy ? "Saving ticket…" : "Save ticket"}</button>
    {context.error ? <span className="error small">{context.error}</span> : others.length > 0 && <span className="muted small">Also saves: {others.join(" · ")}</span>}
  </div>;
}
