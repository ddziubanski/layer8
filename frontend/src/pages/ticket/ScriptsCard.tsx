import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type Ticket } from "../../api/client";
import { busy, RunActionForm, RunList } from "../scripts/Runs";
import { ParamField } from "../scripts/ParamField";

/** Run an action script from the ticket: on its linked devices by default. Runs are noted on the ticket. */
export function ScriptsCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const scripts = useQuery({ queryKey: ["scripts"], queryFn: api.scripts.list });
  const linked = useQuery({ queryKey: ["ticket-devices", ticket.id], queryFn: () => api.devices.forTicket(ticket.id) });
  const runs = useQuery({
    queryKey: ["runs", { ticket_id: ticket.id }],
    queryFn: () => api.runs.list({ ticket_id: ticket.id, limit: 50 }),
    refetchInterval: (q) => (q.state.data?.some(busy) ? 3000 : false),
  });
  const [scriptId, setScriptId] = useState<number | null>(null);
  const actions = (scripts.data ?? []).filter((s) => s.kind === "action" && s.can_run);
  const script = actions.find((s) => s.id === scriptId);
  // The requester's computer first, then the ticket's other linked devices.
  const linkedDevices = (linked.data ?? []).map((l) => l.device);
  const computer = ticket.computer && !linkedDevices.some((d) => d.id === ticket.computer!.id) ? [ticket.computer] : [];
  const devices = [...computer, ...linkedDevices];

  // When a run finishes, the server notes it on the ticket: show that note.
  const open = (runs.data ?? []).filter(busy).length;
  const before = useRef(open);
  useEffect(() => {
    if (open < before.current) onChanged();
    before.current = open;
  }, [open, onChanged]);

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Scripts">
      <div className="lbl" style={{ margin: 0 }}>
        Scripts
      </div>
      {actions.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          No action scripts you can run yet (Scripts).
        </p>
      ) : (
        <label className="field">
          Run a script
          <select value={scriptId ?? ""} onChange={(e) => setScriptId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Choose…</option>
            {actions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.high_risk ? " (needs approval)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {script && (
        <>
          {script.description && <p className="muted small" style={{ margin: 0 }}>{script.description}</p>}
          <RunActionForm
            // Starts on the ticket's devices; a new script or newly linked device starts it fresh.
            key={`${script.id}-${script.current_version}-${devices.map((d) => d.id).join(",")}`}
            script={script}
            ticketId={ticket.id}
            initialDevices={devices}
            onStarted={() => {
              setScriptId(null);
              queryClient.invalidateQueries({ queryKey: ["runs", { ticket_id: ticket.id }] });
              onChanged();
            }}
            params={(vals, set) => script.parameters.map((p) => <ParamField key={p.name} param={p} value={vals[p.name]} onChange={(v) => set({ ...vals, [p.name]: v })} />)}
          />
        </>
      )}
      {(runs.data?.length ?? 0) > 0 && (
        <>
          <div className="lbl" style={{ margin: 0 }}>
            Runs on this ticket
          </div>
          <RunList filter={{ ticket_id: ticket.id }} showScript />
        </>
      )}
    </section>
  );
}
