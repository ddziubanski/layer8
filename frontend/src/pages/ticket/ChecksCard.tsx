// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Ticket } from "../../api/client";
import { when } from "../../format";

type Checks = Awaited<ReturnType<typeof api.checks.list>>;

const STATE: Record<string, string> = { waiting: "Waiting", cleared: "Cleared: verify", confirmed: "Verified" };

/** What this ticket waits on: Layer 8 checks it, records when it clears, and a technician verifies. */
export function ChecksCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket", ticket.id, "checks"];
  const checks = useQuery({ queryKey: key, queryFn: () => api.checks.list(ticket.id), refetchInterval: 60_000 });
  const done = (data: Checks) => {
    queryClient.setQueryData(key, data);
    void queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id] });
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    onChanged();
  };
  const add = useMutation({ mutationFn: (o: Checks["available"][number]) => api.checks.add(ticket.id, { kind: o.kind, device_id: o.device_id, user_id: o.user_id }), onSuccess: done });
  const confirm = useMutation({ mutationFn: (id: number) => api.checks.confirm(ticket.id, id), onSuccess: done });
  const cancel = useMutation({ mutationFn: (id: number) => api.checks.cancel(ticket.id, id), onSuccess: done });
  const error = add.error ?? confirm.error ?? cancel.error;
  const list = checks.data?.checks ?? [];
  const offers = checks.data?.available ?? [];
  const closed = ticket.status === "resolved" || ticket.status === "closed";

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Watching">
      <div className="lbl" style={{ margin: 0 }}>Watching</div>
      {checks.isPending && <p className="muted small" style={{ margin: 0 }}>Loading…</p>}
      {checks.error && <p className="error small" style={{ margin: 0 }}>{checks.error.message}</p>}
      {list.map((c) => (
        <div key={c.id} className="stack" style={{ gap: 2 }} role="group" aria-label={c.label}>
          <div className="row-gap">
            <span className="spacer">{c.label}</span>
            <span className={`chip ${c.state === "cleared" ? "a" : c.state === "confirmed" ? "g" : ""}`}>{STATE[c.state] ?? c.state}</span>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            {c.detail || "Not checked yet"}
            {c.cleared_at ? ` · cleared ${when(c.cleared_at)}` : c.checked_at ? ` · checked ${when(c.checked_at)}` : ""}
            {c.confirmed_by ? ` · verified by ${c.confirmed_by.display_name}` : ""}
          </p>
          {!closed && c.state !== "confirmed" && <div className="row-gap">
            {c.state === "cleared" && <button className="btn" type="button" disabled={confirm.isPending} onClick={() => confirm.mutate(c.id)}>Verified, it&apos;s fixed</button>}
            <button className="linkish small" type="button" disabled={cancel.isPending} onClick={() => cancel.mutate(c.id)}>Stop watching</button>
          </div>}
        </div>
      ))}
      {checks.data && !list.length && !offers.length && (
        <p className="muted small" style={{ margin: 0 }}>Nothing to watch. Link a device that Action1, Active Directory or Nutanix reports on, or a requester whose directory account is locked or disabled.</p>
      )}
      {!closed && offers.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <p className="muted small" style={{ margin: 0 }}>Wait for one of these, checked every few minutes. When it clears this ticket shows Verify; it never closes by itself.</p>
          {offers.map((o) => (
            <div key={`${o.kind}:${o.device_id ?? ""}:${o.user_id ?? ""}`} className="row-gap">
              <span className="spacer small">{o.label} <span className="muted">({o.now})</span></span>
              <button className="btn2" type="button" disabled={add.isPending} onClick={() => add.mutate(o)} aria-label={`Watch: ${o.label}`}>Watch</button>
            </div>
          ))}
        </div>
      )}
      {error && <p className="error small" role="alert" style={{ margin: 0 }}>{error.message}</p>}
    </section>
  );
}
