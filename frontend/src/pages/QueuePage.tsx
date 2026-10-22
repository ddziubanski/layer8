import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type CSSProperties } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { api, type Me, type SavedView, type Ticket, type TicketScope, type TicketSort } from "../api/client";
import { ruleColor } from "../colors";
import { PriorityChip, SlaChip, SourceChip } from "../components/Chips";
import { duration, plural, VIEWS } from "../format";
import { NewTicketDialog } from "./NewTicketDialog";
import { can } from "../permissions";

const PAGE = 100;
const SCOPES: { key: TicketScope; label: string }[] = [
  { key: "mine", label: "My tickets" },
  { key: "groups", label: "My groups" },
  { key: "all", label: "All tickets" },
];
const SCOPE_KEY = "helpdesk.queue.scope";
const QUEUE_KEY = "helpdesk.queue.queue";

/** The queue last chosen in this browser ("" for all queues). */
function savedQueue(): string {
  try {
    return localStorage.getItem(QUEUE_KEY) ?? "";
  } catch {
    return "";
  }
}

function savedScope(): TicketScope {
  try {
    const v = localStorage.getItem(SCOPE_KEY);
    return v === "mine" || v === "groups" || v === "all" ? v : "all";
  } catch {
    return "all";
  }
}
const REFRESH_MS = 30_000;

function isView(v: string | null): v is SavedView {
  return VIEWS.some((view) => view.key === v);
}

// Columns you can sort by, and the order a first click gives (the API's defaults).
const COLUMNS: { key: TicketSort; label: string; first: "asc" | "desc" }[] = [
  { key: "id", label: "ID", first: "desc" },
  { key: "subject", label: "Subject", first: "asc" },
  { key: "requester", label: "Requester", first: "asc" },
  { key: "source", label: "Source", first: "asc" },
  { key: "assignee", label: "Assignee / Queue", first: "asc" },
  { key: "priority", label: "Priority", first: "desc" },
  { key: "age", label: "Age", first: "desc" },
  { key: "due", label: "SLA", first: "asc" },
];

function isSort(v: string | null): v is TicketSort {
  return COLUMNS.some((c) => c.key === v);
}

function Row({ t, me, selected, onSelect }: { t: Ticket; me: Me; selected: boolean; onSelect: (on: boolean) => void }) {
  const navigate = useNavigate();
  // The assignee's colour as a stripe (everyone sees it); my own rules tint the row (only I do).
  const tint = ruleColor(t, me.color_rules ?? [], me.id);
  const style = {
    "--stripe": t.assignee?.ticket_color ?? "transparent",
    ...(tint ? { "--rule": tint } : {}),
  } as CSSProperties;
  const sub = [
    t.parent ? `Part of #${t.parent.id}` : null,
    t.child_count ? `${plural(t.child_count, "child ticket")} (${t.open_child_count} open)` : null,
    t.attachment_count ? plural(t.attachment_count, "attachment") : null,
    t.matched_rule_name ? `Rule: ${t.matched_rule_name}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    // The subject link is the accessible target; the row click is a mouse shortcut.
    <div className={`trow body${tint ? " tinted" : ""}${selected ? " selected" : ""}`} role="row" style={style} onClick={() => navigate(`/tickets/${t.id}`)}>
      <div role="cell" className="pick" onClick={(e) => e.stopPropagation()}>
        {can(me, "ticket_bulk") && <input type="checkbox" aria-label={`Select #${t.id}`} checked={selected} onChange={(e) => onSelect(e.target.checked)} />}
      </div>
      <div role="cell">{t.label}</div>
      <div role="cell" className="subject">
        <Link to={`/tickets/${t.id}`} onClick={(e) => e.stopPropagation()}>
          {t.subject}
        </Link>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div role="cell">{t.requester.display_name}</div>
      <div role="cell">
        <SourceChip source={t.source} />
      </div>
      <div role="cell">
        {t.assignee ? (
          <span className="assignee">
            {t.assignee.ticket_color && <span className="dot" style={{ background: t.assignee.ticket_color }} aria-hidden="true" />}
            {t.assignee.display_name}
          </span>
        ) : (
          <span className="muted">Unassigned</span>
        )}
        <div className="sub">{t.queue.name}</div>
      </div>
      <div role="cell">
        <PriorityChip priority={t.priority} />
      </div>
      <div role="cell" title={new Date(t.created_at).toLocaleString()}>
        {duration(Date.now() - new Date(t.created_at).getTime())}
      </div>
      <div role="cell">
        <SlaChip ticket={t} />
      </div>
    </div>
  );
}

export function QueuePage({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const raw = params.get("view");
  const view: SavedView = isView(raw) ? raw : "open";
  const rawScope = params.get("scope");
  const scope: TicketScope = rawScope === "mine" || rawScope === "groups" || rawScope === "all" ? rawScope : savedScope();
  const queueParam = params.get("queue") ?? savedQueue();
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  // A remembered queue that's gone (or no longer visible) falls back to all queues.
  const queueId = queueParam && (!queues.data || queues.data.some((q) => String(q.id) === queueParam)) ? Number(queueParam) : undefined;
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const rawSort = params.get("sort");
  const sort: TicketSort = isSort(rawSort) ? rawSort : "due";
  const column = COLUMNS.find((c) => c.key === sort)!;
  const dir: "asc" | "desc" = params.get("dir") === "asc" || params.get("dir") === "desc" ? (params.get("dir") as "asc" | "desc") : column.first;
  const [creating, setCreating] = useState(false);

  const counts = useQuery({ queryKey: ["views", scope, queueId], queryFn: () => api.viewCounts(scope, queueId), refetchInterval: REFRESH_MS });
  const page = useQuery({
    queryKey: ["tickets", scope, queueId, view, sort, dir, offset],
    queryFn: () => api.tickets({ scope, queue_id: queueId, view, sort, dir, limit: PAGE, offset }),
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData,
  });

  function go(next: { scope?: TicketScope; queue?: string; view?: SavedView; offset?: number; sort?: TicketSort; dir?: "asc" | "desc" }) {
    const s = next.sort ?? sort;
    const d = next.dir ?? (next.sort ? undefined : dir);
    if (next.scope) {
      try {
        localStorage.setItem(SCOPE_KEY, next.scope);
      } catch {
        // Not remembered; the URL still has it.
      }
    }
    if (next.queue !== undefined) {
      try {
        localStorage.setItem(QUEUE_KEY, next.queue);
      } catch {
        // Not remembered; the URL still has it.
      }
    }
    const q = next.queue ?? (queueId ? String(queueId) : "");
    setSelected(new Set());
    setParams({
      scope: next.scope ?? scope,
      ...(q ? { queue: q } : {}),
      view: next.view ?? view,
      ...(s !== "due" ? { sort: s } : {}),
      ...(d && d !== COLUMNS.find((c) => c.key === s)!.first ? { dir: d } : {}),
      ...(next.offset ? { offset: String(next.offset) } : {}),
    });
  }

  // Click a column: sort by it; click it again: the other way round. Back to page 1 either way.
  function sortBy(key: TicketSort) {
    if (key === sort) go({ sort: key, dir: dir === "asc" ? "desc" : "asc" });
    else go({ sort: key });
  }

  const total = page.data?.total ?? 0;
  const items = page.data?.items ?? [];
  const allPicked = items.length > 0 && items.every((t) => selected.has(t.id));
  const pick = (id: number, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  };
  return (
    <div className="queue-page">
      <div className="page-head">
        <div className="queue-title"><span className="queue-eyebrow">SERVICE DESK / WORKSPACE</span><h1>Ticket queue</h1><span className="queue-subtitle">Prioritize requests and keep work moving.</span></div>
        <div className="views scope" role="group" aria-label="Whose tickets">
          {SCOPES.map((s) => (
            <button key={s.key} type="button" aria-pressed={s.key === scope} onClick={() => go({ scope: s.key })}>
              {s.label}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <select aria-label="Queue" className="queue-switch" value={queueId ? String(queueId) : ""} onChange={(e) => go({ queue: e.target.value, offset: 0 })}>
          <option value="">All queues</option>
          {queues.data?.map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
        <button className="btn" type="button" onClick={() => setCreating(true)}>
          New ticket
        </button>
      </div>

      <div className="views" role="group" aria-label="Saved views">
        {VIEWS.map((v) => (
          <button key={v.key} type="button" aria-pressed={v.key === view} onClick={() => go({ view: v.key })}>
            {v.label}
            {counts.data && (
              <>
                {" "}
                <span className="count">{counts.data[v.key]}</span>
              </>
            )}
          </button>
        ))}
      </div>

      {page.error && <p className="error">{page.error.message}</p>}
      {selected.size > 0 && can(me, "ticket_bulk") && (
        <BulkBar
          ids={[...selected]}
          onDone={() => {
            setSelected(new Set());
            queryClient.invalidateQueries({ queryKey: ["tickets"] });
            queryClient.invalidateQueries({ queryKey: ["views"] });
          }}
          onClear={() => setSelected(new Set())}
        />
      )}
      <div className="table-wrap queue-list" role="table" aria-label="Tickets">
        <div className="trow head" role="row">
          <div role="columnheader" className="pick">
            {can(me, "ticket_bulk") && (
              <input type="checkbox" aria-label="Select all on this page" checked={allPicked} onChange={(e) => setSelected(e.target.checked ? new Set(items.map((t) => t.id)) : new Set())} />
            )}
          </div>
          {COLUMNS.map((c) => (
            <div key={c.key} role="columnheader" aria-sort={c.key === sort ? (dir === "asc" ? "ascending" : "descending") : "none"}>
              <button type="button" className="sort" onClick={() => sortBy(c.key)}>
                {c.label}
                <span className="sort-mark" aria-hidden="true">
                  {c.key === sort ? (dir === "asc" ? " ▲" : " ▼") : ""}
                </span>
              </button>
            </div>
          ))}
        </div>
        {items.map((t) => (
          <Row key={t.id} t={t} me={me} selected={selected.has(t.id)} onSelect={(on) => pick(t.id, on)} />
        ))}
        {page.data && page.data.items.length === 0 && <div className="empty">No tickets in this view.</div>}
      </div>

      {total > PAGE && (
        <div className="pager">
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </span>
          <button className="btn2" type="button" disabled={offset === 0} onClick={() => go({ offset: offset - PAGE })}>
            Previous
          </button>
          <button className="btn2" type="button" disabled={offset + PAGE >= total} onClick={() => go({ offset: offset + PAGE })}>
            Next
          </button>
        </div>
      )}

      {creating && <NewTicketDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

/** Move and/or assign the selected tickets. Each change is noted on its ticket, as when done one by one. */
function BulkBar({ ids, onDone, onClear }: { ids: number[]; onDone: () => void; onClear: () => void }) {
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [queueId, setQueueId] = useState("");
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  const run = useMutation({
    mutationFn: () =>
      api.bulkAssign({
        ticket_ids: ids,
        queue_id: queueId ? Number(queueId) : null,
        assignee_id: assignee && assignee !== "none" ? Number(assignee) : null,
        unassign: assignee === "none",
        note: note.trim() || null,
      }),
    onSuccess: (r) => {
      if (Object.keys(r.failed).length === 0) onDone();
    },
  });
  const failed = Object.entries(run.data?.failed ?? {});
  return (
    <div className="card bulk-bar" role="region" aria-label="Selected tickets">
      <strong>{ids.length} selected</strong>
      <select aria-label="Move to queue" value={queueId} onChange={(e) => setQueueId(e.target.value)}>
        <option value="">Keep their queues</option>
        {queues.data
          ?.filter((q) => q.is_active)
          .map((q) => (
            <option key={q.id} value={q.id}>
              Move to {q.name}
            </option>
          ))}
      </select>
      <select aria-label="Assign to" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
        <option value="">Keep their assignees</option>
        <option value="none">Unassign</option>
        {users.data
          ?.filter((u) => u.role !== "requester" && u.is_active)
          .map((u) => (
            <option key={u.id} value={u.id}>
              Assign to {u.display_name}
            </option>
          ))}
      </select>
      <input aria-label="Reason (optional)" placeholder="Reason (optional, noted on each)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn" type="button" disabled={(!queueId && !assignee) || run.isPending} onClick={() => run.mutate()}>
        Apply
      </button>
      <button className="linkish small" type="button" onClick={onClear}>
        Clear selection
      </button>
      {run.error && <span className="error small">{run.error.message}</span>}
      {failed.length > 0 && (
        <div className="error small" role="alert" style={{ flexBasis: "100%" }}>
          {run.data!.done.length} changed. Not changed:{" "}
          {failed.map(([id, why]) => `#${id} (${why})`).join("; ")}
          <button className="linkish small" type="button" style={{ marginLeft: 8 }} onClick={onDone}>
            OK
          </button>
        </div>
      )}
    </div>
  );
}
