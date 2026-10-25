// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { api, type Layout, type Me, type PO, type POIn } from "../../api/client";
import { ArrangedColumns, arrangedFields, useArrangement, type FieldGroups } from "../../components/Arrange";
import { DevicePicker } from "../../components/DevicePicker";
import { fileSize, when } from "../../format";
import { can } from "../../permissions";

export const PO_STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "n"],
  requested: ["Requested", "a"],
  approved: ["Approved", "g"],
  ordered: ["Ordered", "a"],
  partially_received: ["Partly received", "a"],
  received: ["Received", "g"],
  cancelled: ["Cancelled", "n"],
  closed: ["Closed", "n"],
};

export function POStatusChip({ status }: { status: string }) {
  const [label, tone] = PO_STATUS[status] ?? [status, "n"];
  return <span className={`chip ${tone}`}>{label}</span>;
}

export const money = (n: number, currency = "USD") => {
  try {
    return n.toLocaleString(undefined, { style: "currency", currency });
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
};

/** The Purchase Orders page: search and filter, newest first. */
export function PurchaseOrdersPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filter, setFilter] = useState("open");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const list = useQuery({
    queryKey: ["pos", debounced, filter],
    queryFn: () => api.pos.list({ q: debounced, open_only: filter === "open", status: ["open", "all"].includes(filter) ? undefined : filter }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <div className="page-head row-gap">
        <WorkspaceTitle title="Purchase orders" area="PROCUREMENT" />
        <button className="btn" type="button" onClick={() => navigate("/purchase-orders/new")}>
          New purchase order
        </button>
      </div>
      <div className="row-gap" style={{ marginBottom: 12 }}>
        <input type="search" style={{ maxWidth: 420 }} placeholder="Search PO number, title, vendor, item or part number" aria-label="Search purchase orders" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
          <option value="open">Open (not received, closed or cancelled)</option>
          <option value="all">All</option>
          {Object.entries(PO_STATUS).map(([k, [label]]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
        <span className="muted small">{list.data?.total ?? 0} purchase order(s)</span>
      </div>
      {list.error && <p className="error">{list.error.message}</p>}
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>PO</th>
              <th>Title</th>
              <th>Vendor</th>
              <th>Department</th>
              <th>Status</th>
              <th>Expected</th>
              <th className="num">Received</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {list.data?.items.map((po) => (
              <tr key={po.id}>
                <td className="nowrap">
                  <Link to={`/purchase-orders/${po.id}`}>{po.label}</Link>
                </td>
                <td>{po.title}</td>
                <td>{po.vendor?.name ?? "–"}</td>
                <td>{po.department?.label ?? "–"}</td>
                <td>
                  <POStatusChip status={po.status} />
                </td>
                <td>{po.expected_date ? new Date(`${po.expected_date}T00:00`).toLocaleDateString() : "–"}</td>
                <td className="num">
                  {po.lines_received}/{po.lines_count}
                </td>
                <td className="num">{money(po.total, po.currency)}</td>
              </tr>
            ))}
            {list.data?.items.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
                  {debounced ? "Nothing matches." : "No purchase orders here yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

type Line = POIn["lines"] extends (infer L)[] | undefined ? L : never;
const blankLine = (): Line => ({ item: "", description: "", part_number: "", quantity: 1, unit_price: 0, tax_rate: 0 });

function toForm(po: PO | null): POIn {
  if (!po) return { title: "", vendor_id: null, department_id: null, requested_by_id: null, approver_id: null, order_date: null, expected_date: null, ship_to: "", budget_code: "", vendor_reference: "", shipping: 0, currency: "USD", terms: "", notes: "", lines: [blankLine()], ticket_ids: [], device_ids: [] };
  return {
    title: po.title, vendor_id: po.vendor?.id ?? null, department_id: po.department?.id ?? null, requested_by_id: po.requested_by?.id ?? null, approver_id: po.approver?.id ?? null,
    order_date: po.order_date, expected_date: po.expected_date, ship_to: po.ship_to, budget_code: po.budget_code, vendor_reference: po.vendor_reference, shipping: po.shipping,
    currency: po.currency, terms: po.terms, notes: po.notes,
    lines: po.lines.map((l) => ({ id: l.id, item: l.item, description: l.description, part_number: l.part_number, quantity: l.quantity, unit_price: l.unit_price, tax_rate: l.tax_rate })),
    ticket_ids: po.tickets.map((t) => t.id), device_ids: po.devices.map((d) => d.id),
  };
}

/** One purchase order: edit it, move it along, receive it, its files and history. */
// The order form's arrangeable fields (app.layouts FIELDS["po"]).
export const PO_FIELDS: FieldGroups = {
  order_top: {
    panel: "order",
    title: "Fields at the top",
    labels: {
      vendor: "Vendor", vendor_reference: "Vendor quote / order number", department: "Department", requested_by: "Requested by", approver: "Approver (manager)",
      budget_code: "Budget / cost code", order_date: "Ordered on", expected_date: "Expected delivery", currency: "Currency",
    },
  },
  order_bottom: { panel: "order", title: "Fields at the bottom", labels: { ship_to: "Ship to", terms: "Terms", notes: "Notes" } },
};
const PO_WIDE = ["ship_to", "terms", "notes"];

// Until the saved layout loads (the same as app.layouts' usual one).
const USUAL_LAYOUT = { top: [], main: ["order"], side: ["receive", "heads_up", "files", "history"], bottom: [], hidden: [], fields: {} };

export function PurchaseOrderPage({ me }: { me: Me }) {
  const { poId } = useParams();
  const isNew = poId === "new";
  const id = Number(poId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // From a ticket's Expenses: the new PO starts linked to it.
  const fromTicket = (useLocation().state as { ticketId?: number } | null)?.ticketId;
  const po = useQuery({ queryKey: ["po", id], queryFn: () => api.pos.get(id), enabled: !isNew });
  const arrangement = useArrangement("po");
  if (!isNew && po.error) return <p className="error">{po.error.message}</p>;
  if (!isNew && !po.data) return <p className="muted">Loading…</p>;
  const data = isNew ? null : po.data!;
  const refresh = (p: PO) => {
    queryClient.setQueryData(["po", p.id], p);
    queryClient.invalidateQueries({ queryKey: ["pos"] });
  };

  return (
    <section className="purchase-workspace">
      <header className="purchase-workspace-head row-gap">
        <div className="spacer">
          <div className="small">
            <Link to="/purchase-orders">Purchase orders</Link>
          </div>
          <p className="workspace-area">PROCUREMENT WORKSPACE</p>
          <h1>
            {data ? (
              <>
                {data.label} {data.title} <POStatusChip status={data.status} />
              </>
            ) : (
              "New purchase order"
            )}
          </h1>
        </div>
        {data && !arrangement.editing && (
          <button className="linkish small" type="button" data-arrange-page="po" onClick={() => arrangement.setEditing(true)}>
            Arrange this page
          </button>
        )}
        {data && (
          <a className="btn2" href={`/purchase-orders/${data.id}/receiving`} target="_blank" rel="noreferrer">
            Receiving sheet
          </a>
        )}
      </header>
      {data && <dl className="purchase-summary" aria-label="Order summary">
        <div><dt>Supplier</dt><dd>{data.vendor?.name || "Not selected"}</dd></div>
        <div><dt>Expected delivery</dt><dd>{data.expected_date ? new Date(`${data.expected_date}T00:00`).toLocaleDateString() : "Not scheduled"}</dd></div>
        <div><dt>Order total</dt><dd>{money(data.total, data.currency)}</dd></div>
        <div><dt>Lines fully received</dt><dd>{data.lines_received} / {data.lines_count}</dd></div>
      </dl>}
      {data && <StatusBar po={data} onChanged={refresh} />}
      {data ? (
        <div className="po-layout">
          <ArrangedColumns
            page="po"
            me={me}
            editing={arrangement.editing}
            onDone={() => arrangement.setEditing(false)}
            layout={arrangement.layout?.effective ?? USUAL_LAYOUT}
            required={arrangement.layout?.required ?? ["order"]}
            hasMine={!!arrangement.layout?.mine}
            fieldGroups={PO_FIELDS}
            panels={(shown) => ({
              order: { label: "The order", node: <POForm key={data.updated_at} po={data} ticketIds={[]} onSaved={refresh} fields={shown} /> },
              // Only once it's ordered.
              receive: { label: "Receive", node: ["ordered", "partially_received", "received"].includes(data.status) ? <ReceivePanel po={data} onChanged={refresh} /> : null },
              heads_up: { label: "Heads-up to receiving", node: <HeadsUp po={data} onChanged={refresh} /> },
              files: { label: "Files", node: <Files po={data} onChanged={() => po.refetch()} /> },
              history: { label: "History", node: <History po={data} /> },
            })}
          />
          {can(me, "po_delete") && (
            <div style={{ gridColumn: "1 / -1" }}>
              <DeletePO po={data} />
            </div>
          )}
        </div>
      ) : (
        <div className="po-layout">
          <POForm key="new" po={null} fields={arrangement.layout?.effective} ticketIds={fromTicket ? [fromTicket] : []} onSaved={(p) => (refresh(p), navigate(`/purchase-orders/${p.id}`, { replace: true }))} />
        </div>
      )}
    </section>
  );
}

function StatusBar({ po, onChanged }: { po: PO; onChanged: (p: PO) => void }) {
  const move = useMutation({ mutationFn: (s: Parameters<typeof api.pos.setStatus>[1]) => api.pos.setStatus(po.id, s), onSuccess: onChanged });
  const b = (s: Parameters<typeof api.pos.setStatus>[1], label: string, primary = false) => (
    <button key={s} className={primary ? "btn" : "btn2"} type="button" disabled={move.isPending} onClick={() => move.mutate(s)}>
      {label}
    </button>
  );
  const buttons = [];
  if (po.status === "draft") buttons.push(b("requested", "Request approval", true));
  if (["draft", "requested"].includes(po.status) && po.can_approve) buttons.push(b("approved", "Approve", true));
  if (["draft", "requested", "approved"].includes(po.status) && (!po.approver || po.status === "approved")) buttons.push(b("ordered", "Mark ordered", po.status === "approved"));
  if (["received", "partially_received", "ordered"].includes(po.status)) buttons.push(b("closed", "Close"));
  if (!["cancelled", "closed", "received"].includes(po.status)) buttons.push(b("cancelled", "Cancel PO"));
  if (["cancelled", "closed"].includes(po.status)) buttons.push(b("draft", "Reopen as draft"));
  return (
    <div className="card row-gap purchase-actions" role="group" aria-label="Order actions" style={{ margin: "0 0 12px", padding: 10 }}>
      {buttons}
      {po.approver && !["approved", "ordered", "partially_received", "received", "closed"].includes(po.status) && (
        <span className="muted small">Needs approval by {po.approver.display_name} before it's ordered.</span>
      )}
      {move.error && <span className="error small">{move.error.message}</span>}
    </div>
  );
}

function POForm({ po, ticketIds, onSaved, fields }: { po: PO | null; ticketIds: number[]; onSaved: (p: PO) => void; fields?: Layout }) {
  const [f, setF] = useState<POIn>(() => (po ? toForm(po) : { ...toForm(null), ticket_ids: ticketIds }));
  const vendors = useQuery({ queryKey: ["vendors", ""], queryFn: () => api.vendors.list() });
  const departments = useQuery({ queryKey: ["field-options", "department"], queryFn: () => api.fieldOptions("department") });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [devices, setDevices] = useState<{ id: number; name: string }[]>(po?.devices ?? []);
  const [ticketNo, setTicketNo] = useState("");
  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, device_ids: devices.map((d) => d.id) };
      return po ? api.pos.update(po.id, body) : api.pos.create(body);
    },
    onSuccess: onSaved,
  });
  const lines = f.lines ?? [];
  const setLine = (i: number, over: Partial<Line>) => setF({ ...f, lines: lines.map((l, j) => (j === i ? { ...l, ...over } : l)) });
  const net = lines.reduce((s, l) => s + (l.quantity ?? 0) * (l.unit_price ?? 0), 0);
  const tax = lines.reduce((s, l) => s + (l.quantity ?? 0) * (l.unit_price ?? 0) * ((l.tax_rate ?? 0) / 100), 0);
  const staff = (users.data ?? []).filter((u) => u.role !== "requester" && u.is_active);
  const received = (lineId?: number | null) => po?.lines.find((l) => l.id === lineId)?.received_quantity ?? 0;
  const text = (k: keyof POIn, label: string, type = "text") => (
    <label className="field">
      {label}
      <input type={type} value={(f[k] as string | null) ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value || (type === "date" ? null : "") })} />
    </label>
  );

  const top: Record<string, ReactNode> = {
    vendor: (
      <label className="field">
        Vendor
        <select value={f.vendor_id ?? ""} onChange={(e) => setF({ ...f, vendor_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">–</option>
          {vendors.data?.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
    ),
    vendor_reference: text("vendor_reference", "Vendor quote / order number"),
    department: (
      <label className="field">
        Department
        <select value={f.department_id ?? ""} onChange={(e) => setF({ ...f, department_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">–</option>
          {departments.data?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.label}
            </option>
          ))}
        </select>
      </label>
    ),
    requested_by: (
      <label className="field">
        Requested by
        <select value={f.requested_by_id ?? ""} onChange={(e) => setF({ ...f, requested_by_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">–</option>
          {(users.data ?? []).filter((u) => u.is_active).map((u) => (
            <option key={u.id} value={u.id}>
              {u.display_name}
            </option>
          ))}
        </select>
      </label>
    ),
    approver: (
      <label className="field">
        Approver (manager)
        <select value={f.approver_id ?? ""} onChange={(e) => setF({ ...f, approver_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">No approval needed</option>
          {staff.map((u) => (
            <option key={u.id} value={u.id}>
              {u.display_name}
            </option>
          ))}
        </select>
      </label>
    ),
    budget_code: text("budget_code", "Budget / cost code"),
    order_date: text("order_date", "Ordered on", "date"),
    expected_date: text("expected_date", "Expected delivery", "date"),
    currency: (
      <label className="field">
        Currency
        <input maxLength={3} value={f.currency ?? "USD"} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} />
      </label>
    ),
  };
  const bottom: Record<string, ReactNode> = {
    ship_to: (
      <label className="field">
        Ship to
        <textarea rows={2} value={f.ship_to ?? ""} onChange={(e) => setF({ ...f, ship_to: e.target.value })} placeholder="Site, building, room, attention" />
      </label>
    ),
    terms: (
      <label className="field">
        Terms <span className="muted small">(payment, warranty, delivery)</span>
        <textarea rows={2} value={f.terms ?? ""} onChange={(e) => setF({ ...f, terms: e.target.value })} />
      </label>
    ),
    notes: (
      <label className="field">
        Notes
        <textarea rows={3} value={f.notes ?? ""} onChange={(e) => setF({ ...f, notes: e.target.value })} />
      </label>
    ),
  };
  // Arrange this page: the order, width and visibility of these fields.
  const place = (group: string, map: Record<string, ReactNode>) =>
    arrangedFields(fields, group, Object.keys(PO_FIELDS[group].labels), PO_WIDE).map(({ id, wide }) => (
      <div key={id} style={wide ? { gridColumn: "1 / -1" } : undefined}>
        {map[id]}
      </div>
    ));

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label="Purchase order"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="field">
        Title
        <input required maxLength={300} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Monitors for Finance" />
      </label>
      <div className="form-grid">{place("order_top", top)}</div>
      <fieldset className="filter-box">
        <legend>What's ordered</legend>
        <div className="stack" style={{ gap: 8 }}>
          <div className="line-row po-line-row muted small" aria-hidden="true">
            <span>Item</span>
            <span>Part number</span>
            <span>Qty</span>
            <span>Unit price</span>
            <span>Tax %</span>
            <span className="num">Line total</span>
            <span />
          </div>
          {lines.map((l, i) => {
            const got = received(l.id);
            const total = (l.quantity ?? 0) * (l.unit_price ?? 0) * (1 + (l.tax_rate ?? 0) / 100);
            return (
              <div key={l.id ?? `new-${i}`} className="line-row po-line-row" role="group" aria-label={`Line ${i + 1}`}>
                <input aria-label="Item" required placeholder="Item" value={l.item} onChange={(e) => setLine(i, { item: e.target.value })} />
                <input aria-label="Part number" placeholder="Part / SKU" value={l.part_number ?? ""} onChange={(e) => setLine(i, { part_number: e.target.value })} />
                <input aria-label="Quantity" type="number" min={got || 0.001} step="any" value={l.quantity} onChange={(e) => setLine(i, { quantity: Number(e.target.value) })} />
                <input aria-label="Unit price" type="number" min={0} step="0.01" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: Number(e.target.value) })} />
                <input aria-label="Tax %" type="number" min={0} max={100} step="any" value={l.tax_rate ?? 0} onChange={(e) => setLine(i, { tax_rate: Number(e.target.value) })} />
                <span className="num">{money(total, f.currency ?? "USD")}</span>
                {got > 0 ? (
                  <span className="muted small" title="Received lines stay">
                    {got} in
                  </span>
                ) : (
                  <button className="linkish small" type="button" aria-label={`Remove line ${i + 1}`} onClick={() => setF({ ...f, lines: lines.filter((_, j) => j !== i) })}>
                    Remove
                  </button>
                )}
              </div>
            );
          })}
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => setF({ ...f, lines: [...lines, blankLine()] })}>
              Add a line
            </button>
            <span className="spacer" />
            <label className="inline small">
              Shipping
              <input aria-label="Shipping" type="number" min={0} step="0.01" style={{ width: 110 }} value={f.shipping ?? 0} onChange={(e) => setF({ ...f, shipping: Number(e.target.value) })} />
            </label>
          </div>
          <div className="po-totals small">
            <span>Subtotal {money(net, f.currency ?? "USD")}</span>
            <span>Tax {money(tax, f.currency ?? "USD")}</span>
            <strong>Total {money(net + tax + (f.shipping ?? 0), f.currency ?? "USD")}</strong>
          </div>
        </div>
      </fieldset>
      <fieldset className="filter-box">
        <legend>For</legend>
        <div className="stack" style={{ gap: 6 }}>
          <div className="row-gap">
            {(f.ticket_ids ?? []).map((tid) => {
              const t = po?.tickets.find((x) => x.id === tid);
              return (
                <span key={tid} className="chip n">
                  <Link to={`/tickets/${tid}`}>{t?.label ?? `#${tid}`}</Link> {t?.subject}{" "}
                  <button className="linkish small" type="button" aria-label={`Remove ticket ${tid}`} onClick={() => setF({ ...f, ticket_ids: (f.ticket_ids ?? []).filter((x) => x !== tid) })}>
                    ×
                  </button>
                </span>
              );
            })}
            <input aria-label="Add a ticket by number" placeholder="Ticket number" inputMode="numeric" style={{ width: 140 }} value={ticketNo} onChange={(e) => setTicketNo(e.target.value)} />
            <button
              className="btn2"
              type="button"
              disabled={!/^\d+$/.test(ticketNo.replace(/\D/g, "")) || !ticketNo}
              onClick={() => {
                const n = Number(ticketNo.replace(/\D/g, ""));
                if (!(f.ticket_ids ?? []).includes(n)) setF({ ...f, ticket_ids: [...(f.ticket_ids ?? []), n] });
                setTicketNo("");
              }}
            >
              Add ticket
            </button>
          </div>
          <div className="row-gap">
            {devices.map((d) => (
              <span key={d.id} className="chip n">
                {d.name}{" "}
                <button className="linkish small" type="button" aria-label={`Remove ${d.name}`} onClick={() => setDevices(devices.filter((x) => x.id !== d.id))}>
                  ×
                </button>
              </span>
            ))}
          </div>
          <DevicePicker label="Add a device" exclude={devices.map((d) => d.id)} onPick={(d) => setDevices([...devices, d])} />
        </div>
      </fieldset>
      <div className="form-grid">{place("order_bottom", bottom)}</div>
      {save.error && <p className="error">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {po ? "Save" : "Create purchase order"}
        </button>
        {save.isSuccess && <span className="muted small">Saved.</span>}
      </div>
    </form>
  );
}

function ReceivePanel({ po, onChanged }: { po: PO; onChanged: (p: PO) => void }) {
  const [now, setNow] = useState<Record<number, string>>({});
  const [note, setNote] = useState("");
  const receive = useMutation({
    mutationFn: () =>
      api.pos.receive(po.id, {
        lines: Object.entries(now)
          .filter(([, v]) => v !== "" && Number(v) !== 0)
          .map(([id, v]) => ({ line_id: Number(id), quantity: Number(v) })),
        note,
      }),
    onSuccess: (p) => {
      setNow({});
      setNote("");
      onChanged(p);
    },
  });
  const allIn = () => setNow(Object.fromEntries(po.lines.map((l) => [l.id, String(Math.max(0, l.quantity - l.received_quantity))])));
  return (
    <section className="card stack" style={{ margin: 0, gap: 8 }} aria-label="Receive">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Receive
        </div>
        <button className="linkish small" type="button" onClick={allIn}>
          Everything still due
        </button>
      </div>
      {po.lines.map((l) => (
        <div key={l.id} className="row-gap">
          <span className="spacer">
            {l.item} <span className="muted small">{l.received_quantity}/{l.quantity} in</span>
          </span>
          <input aria-label={`${l.item}: arrived now`} type="number" step="any" style={{ width: 90 }} value={now[l.id] ?? ""} onChange={(e) => setNow({ ...now, [l.id]: e.target.value })} />
        </div>
      ))}
      <input aria-label="Receiving note" placeholder="Note (optional): damaged box, backorder…" value={note} onChange={(e) => setNote(e.target.value)} />
      {receive.error && <p className="error small">{receive.error.message}</p>}
      <div>
        <button className="btn" type="button" disabled={receive.isPending || !Object.values(now).some((v) => v && Number(v) !== 0)} onClick={() => receive.mutate()}>
          Mark received
        </button>
      </div>
    </section>
  );
}

function HeadsUp({ po, onChanged }: { po: PO; onChanged: (p: PO) => void }) {
  const [to, setTo] = useState("");
  const send = useMutation({ mutationFn: () => api.pos.headsUp(po.id, { to: to || null, message: "" }), onSuccess: (p) => (setTo(""), onChanged(p)) });
  return (
    <section className="card stack" style={{ margin: 0, gap: 8 }} aria-label="Receiving">
      <div className="lbl" style={{ margin: 0 }}>
        Receiving
      </div>
      {po.receiving_url ? (
        <span className="small">
          Code <strong className="code">{po.receiving_code}</strong> · the receiving sheet has the link and a QR code.
        </span>
      ) : (
        <span className="muted small">The public receiving page is off (Settings &gt; Purchasing).</span>
      )}
      <div className="row-gap">
        <input aria-label="Heads-up to" type="email" placeholder="Receiving's address (from Settings)" value={to} onChange={(e) => setTo(e.target.value)} style={{ flex: 1 }} />
        <button className="btn2" type="button" disabled={send.isPending} onClick={() => send.mutate()}>
          Send heads-up
        </button>
      </div>
      {send.isSuccess && <span className="muted small">Sent.</span>}
      {send.error && <span className="error small">{send.error.message}</span>}
    </section>
  );
}

function Files({ po, onChanged }: { po: PO; onChanged: () => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const upload = useMutation({ mutationFn: (f: File) => api.pos.upload(po.id, f), onSettled: onChanged });
  const remove = useMutation({ mutationFn: (id: string) => api.pos.removeFile(id), onSettled: onChanged });
  return (
    <section className="card stack" style={{ margin: 0, gap: 8 }} aria-label="Files">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Files <span className="muted">(quotes, invoices, packing slips)</span>
        </div>
        <input ref={picker} type="file" hidden aria-label="Add a file" onChange={(e) => (e.target.files?.[0] && upload.mutate(e.target.files[0]), (e.target.value = ""))} />
        <button className="btn2" type="button" disabled={upload.isPending} onClick={() => picker.current?.click()}>
          Add a file
        </button>
      </div>
      {po.attachments.length === 0 && <span className="muted small">None yet.</span>}
      {po.attachments.map((a) => (
        <div key={a.id} className="row-gap small">
          <a className="spacer" href={`/api/po-files/${a.id}/view`} target="_blank" rel="noreferrer">
            {a.filename}
          </a>
          <span className="muted">{fileSize(a.size_bytes)}</span>
          <a href={`/api/po-files/${a.id}/download`}>Download</a>
          <button className="linkish small" type="button" onClick={() => window.confirm(`Remove ${a.filename}?`) && remove.mutate(a.id)}>
            Remove
          </button>
        </div>
      ))}
      {(upload.error ?? remove.error) && <span className="error small">{(upload.error ?? remove.error)!.message}</span>}
    </section>
  );
}

function History({ po }: { po: PO }) {
  const say = (e: PO["events"][number]) => {
    const d = e.data as Record<string, string | string[]>;
    switch (e.type) {
      case "created":
        return "Created";
      case "edited":
        return `Edited ${(d.fields as string[]).join(", ").replaceAll("_", " ")}`;
      case "status":
        return `${PO_STATUS[d.before as string]?.[0] ?? d.before} → ${PO_STATUS[d.after as string]?.[0] ?? d.after}${d.note ? `: ${d.note}` : ""}`;
      case "received":
        return `${d.summary}${d.note ? ` ${d.note}` : ""}`;
      case "file":
        return `Added ${d.filename}${d.by ? ` (${d.by})` : ""}`;
      case "file_removed":
        return `Removed ${d.filename}`;
      case "heads_up":
        return `Heads-up sent to ${d.to}`;
      default:
        return e.type;
    }
  };
  return (
    <section className="card" style={{ margin: 0 }} aria-label="History">
      <div className="lbl">History</div>
      <ol className="history">
        {po.events.map((e) => (
          <li key={e.id}>
            <span className="muted">{when(e.created_at)}</span>
            <span>
              {say(e)} <span className="muted">· {e.actor?.display_name ?? "System"}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function DeletePO({ po }: { po: PO }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const remove = useMutation({ mutationFn: () => api.pos.remove(po.id), onSuccess: () => (queryClient.invalidateQueries({ queryKey: ["pos"] }), navigate("/purchase-orders")) });
  return (
    <div>
      <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${po.label}? Use Cancel PO instead to keep its history.`) && remove.mutate()}>
        Delete this purchase order
      </button>
      {remove.error && <p className="error small">{remove.error.message}</p>}
    </div>
  );
}
