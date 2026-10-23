import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, type SavedView, type TicketScope } from "../api/client";
import { PriorityChip, SlaChip } from "../components/Chips";

const metrics: { view: SavedView; label: string; detail: string }[] = [
  { view: "open", label: "Open tickets", detail: "Current workload" },
  { view: "unassigned", label: "Unassigned", detail: "Ready for ownership" },
  { view: "sla_at_risk", label: "SLA at risk", detail: "Needs attention" },
  { view: "waiting_on_user", label: "Waiting on user", detail: "Awaiting a response" },
];

export function DashboardPage() {
  const [params, setParams] = useSearchParams();
  const requestedScope = params.get("scope");
  const scope: TicketScope = requestedScope === "groups" || requestedScope === "all" ? requestedScope : "mine";
  const queue = params.get("queue") ?? "";
  const queueId = /^\d+$/.test(queue) && Number(queue) > 0 ? Number(queue) : undefined;
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const counts = useQuery({ queryKey: ["views", scope, queueId], queryFn: () => api.viewCounts(scope, queueId), refetchInterval: 30_000 });
  const attention = useQuery({ queryKey: ["dashboard-attention", scope, queueId], queryFn: () => api.tickets({ scope, queue_id: queueId, view: "sla_at_risk", sort: "due", dir: "asc", limit: 6, offset: 0 }), refetchInterval: 30_000 });
  // Explicit queue= clears the queue remembered by the ticket workspace.
  const queueLink = (view: SavedView) => `/?${new URLSearchParams({ scope, queue: queueId ? String(queueId) : "", view })}`;
  const update = (key: string, value: string) => setParams({ scope, queue, [key]: value });
  return <section className="operations-dashboard">
    <header className="dashboard-heading"><div><p className="eyebrow">SERVICE OPERATIONS</p><h1>Dashboard</h1><p className="muted">A live view of your workload and tickets needing attention.</p></div><Link className="button" to={queueLink("open")}>Open ticket workspace →</Link></header>
    <div className="dashboard-filters">
      <label>Workload <select value={scope} onChange={e => update("scope", e.target.value)}><option value="mine">My tickets</option><option value="groups">My groups</option><option value="all">All tickets</option></select></label>
      <label>Queue <select value={queueId ? String(queueId) : ""} onChange={e => update("queue", e.target.value)}><option value="">All accessible queues</option>{queues.data?.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}</select></label>
      <span className="muted">Refreshes every 30 seconds</span>
    </div>
    {queues.isError && <p role="alert">Queue choices could not be loaded. <button onClick={() => queues.refetch()}>Retry queues</button></p>}
    {counts.isError ? <p role="alert">Workload counts could not be loaded. <button onClick={() => counts.refetch()}>Retry counts</button></p> : <div className="dashboard-metrics" aria-label="Workload counts" aria-busy={counts.isPending}>{metrics.map(m => <Link key={m.view} className={`dashboard-metric ${m.view === "sla_at_risk" ? "attention" : ""}`} to={queueLink(m.view)}><span>{m.label}</span><strong>{counts.data?.[m.view] ?? "…"}</strong><span className="muted">{m.detail} →</span></Link>)}</div>}
    <section className="dashboard-attention"><header><div><h2>Needs attention</h2><p className="muted">Up to six SLA-risk tickets, ordered by due date.</p></div><Link to={queueLink("sla_at_risk")}>View all →</Link></header>
      {attention.isPending ? <p role="status">Loading attention list…</p> : attention.isError ? <p role="alert">Attention list could not be loaded. <button onClick={() => attention.refetch()}>Retry attention list</button></p> : attention.data?.items.length ? <ul>{attention.data.items.map(t => <li key={t.id}><div><Link to={`/tickets/${t.id}`}><span className="muted">{t.label}</span> {t.subject}</Link><p className="muted">{t.queue.name} · {t.assignee?.display_name ?? "Unassigned"}</p></div><div className="dashboard-ticket-chips"><PriorityChip priority={t.priority} /><SlaChip ticket={t} long /></div></li>)}</ul> : <p className="dashboard-empty">No SLA-risk tickets in this workload.</p>}
    </section>
    <p className="muted">Counts use your current access and the selected workload. Restricted queues remain subject to existing permissions.</p>
  </section>;
}
