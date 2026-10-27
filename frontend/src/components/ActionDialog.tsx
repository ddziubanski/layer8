// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useId, useRef, type ReactNode } from "react";

/** Native modal supplies focus containment, Escape and return-focus behavior. */
export function ActionDialog({ title, busy = false, onClose, children }: { title: string; busy?: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current!;
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    return () => { dialog.close?.(); };
  }, []);
  return <dialog ref={ref} className="dialog action-dialog" aria-labelledby={id} aria-modal="true" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <h2 id={id}>{title}</h2>{children}
  </dialog>;
}
