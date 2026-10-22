import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type ExpenseIn, type Ticket } from "../../api/client";

const BLANK: ExpenseIn = { description: "", vendor_id: null, quantity: 1, unit_cost: 0, po_id: null, notes: "" };
const money = (n: number) => n.toLocaleString(undefined, { style: "currency", currency: "USD" });

/** What fixing this ticket costs: expense lines and linked purchase orders, with a total. */
export function ExpensesCard({ ticket }: { ticket: Ticket }) {
  const queryClient = useQueryClient();
  const key = ["ticket-expenses", ticket.id];
  const data = useQuery({ queryKey: key, queryFn: () => api.expenses.forTicket(ticket.id) });
  const vendors = useQuery({ queryKey: ["vendors", ""], queryFn: () => api.vendors.list() });
  const [adding, setAdding] = useState<ExpenseIn | null>(null);
  const done = { onSuccess: (r: Awaited<ReturnType<typeof api.expenses.forTicket>>) => (queryClient.setQueryData(key, r), setAdding(null)) };
  const add = useMutation({ mutationFn: (e: ExpenseIn) => api.expenses.add(ticket.id, e), ...done });
  const remove = useMutation({ mutationFn: (id: number) => api.expenses.remove(ticket.id, id), ...done });
  const d = data.data;

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Expenses">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Expenses {d && d.total > 0 && <span className="muted">· {money(d.total)}</span>}
        </div>
        {!adding && (
          <>
            <Link className="btn2" to="/purchase-orders/new" state={{ ticketId: ticket.id }}>
              New purchase order
            </Link>
            <button className="btn2" type="button" onClick={() => setAdding({ ...BLANK })}>
              Add an expense
            </button>
          </>
        )}
      </div>
      {d && d.expenses.length === 0 && d.purchase_orders.length === 0 && !adding && <p className="muted small" style={{ margin: 0 }}>Nothing yet: parts, courier, a purchase order…</p>}
      {d && d.expenses.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th>What</th>
              <th>Vendor</th>
              <th className="num">Qty</th>
              <th className="num">Each</th>
              <th className="num">Total</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {d.expenses.map((e) => (
              <tr key={e.id}>
                <td>
                  {e.description}
                  {e.po_id && (
                    <>
                      {" "}
                      · <Link to={`/purchase-orders/${e.po_id}`}>{e.po_label}</Link>
                    </>
                  )}
                </td>
                <td>{e.vendor?.name ?? "–"}</td>
                <td className="num">{e.quantity}</td>
                <td className="num">{money(e.unit_cost)}</td>
                <td className="num">{money(e.total)}</td>
                <td>
                  <button className="linkish small" type="button" aria-label={`Remove ${e.description}`} onClick={() => remove.mutate(e.id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {d && d.purchase_orders.length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          <span className="muted small">Purchase orders for this ticket</span>
          {d.purchase_orders.map((p) => (
            <div key={p.id} className="row-gap small">
              <Link to={`/purchase-orders/${p.id}`}>{p.label}</Link>
              <span className="spacer">{p.title}</span>
              <span className="muted">
                {p.lines_received}/{p.lines_count} in
              </span>
              <span>{money(p.total)}</span>
            </div>
          ))}
        </div>
      )}
      {adding && (
        <form
          className="line-row expense-row"
          aria-label="New expense"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate(adding);
          }}
        >
          <input aria-label="What" required autoFocus placeholder="What (e.g. replacement PSU)" value={adding.description} onChange={(e) => setAdding({ ...adding, description: e.target.value })} />
          <select aria-label="Vendor" value={adding.vendor_id ?? ""} onChange={(e) => setAdding({ ...adding, vendor_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Vendor</option>
            {vendors.data?.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
          <input aria-label="Quantity" type="number" min={0.001} step="any" value={adding.quantity} onChange={(e) => setAdding({ ...adding, quantity: Number(e.target.value) })} />
          <input aria-label="Cost each" type="number" min={0} step="0.01" value={adding.unit_cost} onChange={(e) => setAdding({ ...adding, unit_cost: Number(e.target.value) })} />
          <select aria-label="On purchase order" value={adding.po_id ?? ""} onChange={(e) => setAdding({ ...adding, po_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">No PO</option>
            {d?.purchase_orders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          <span className="row-gap">
            <button className="btn" type="submit" disabled={add.isPending}>
              Add
            </button>
            <button className="linkish small" type="button" onClick={() => setAdding(null)}>
              Cancel
            </button>
          </span>
        </form>
      )}
      {(add.error ?? remove.error) && <p className="error small">{(add.error ?? remove.error)!.message}</p>}
    </section>
  );
}
