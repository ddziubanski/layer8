import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api, type PublicPO } from "../../api/client";
import { Brand, useBranding } from "../../branding";

/** A printable sheet for the receiving clerk: what's expected, and how to mark it received
 * (QR code, the address written out, the PO number and its code) for when there's no scanner. */
export function ReceivingSheet() {
  const id = Number(useParams().poId);
  const brand = useBranding();
  const po = useQuery({ queryKey: ["po", id], queryFn: () => api.pos.get(id) });
  const [qr, setQr] = useState<string | null>(null);
  const url = po.data?.receiving_url ?? null;
  const absolute = url && url.startsWith("/") ? `${window.location.origin}${url}` : url;
  useEffect(() => {
    if (absolute) QRCode.toDataURL(absolute, { margin: 1, width: 220 }).then(setQr, () => setQr(null));
  }, [absolute]);
  if (po.error) return <p className="error">{po.error.message}</p>;
  const d = po.data;
  if (!d) return null;
  const pageAddress = absolute ? absolute.split("?")[0] : null;

  return (
    <div className="receiving-sheet">
      <div className="no-print row-gap" style={{ marginBottom: 16 }}>
        <button className="btn" type="button" onClick={() => window.print()}>
          Print
        </button>
        <span className="muted small">Give this to whoever takes in deliveries.</span>
      </div>
      <header className="row-gap">
        <Brand b={brand} />
        <span className="spacer" />
        <strong>Receiving sheet</strong>
      </header>
      <h1>
        {d.label} · {d.title}
      </h1>
      <div className="sheet-facts">
        <div>
          <span className="muted">Vendor</span> {d.vendor?.name ?? "–"}
        </div>
        <div>
          <span className="muted">Expected</span> {d.expected_date ? new Date(`${d.expected_date}T00:00`).toLocaleDateString() : "–"}
        </div>
        <div>
          <span className="muted">Their reference</span> {d.vendor_reference || "–"}
        </div>
        {d.ship_to && (
          <div style={{ gridColumn: "1 / -1", whiteSpace: "pre-wrap" }}>
            <span className="muted">Deliver to</span> {d.ship_to}
          </div>
        )}
      </div>
      <table className="grid sheet-lines">
        <thead>
          <tr>
            <th>Item</th>
            <th>Part number</th>
            <th className="num">Ordered</th>
            <th className="num">Already in</th>
            <th>Arrived (count)</th>
          </tr>
        </thead>
        <tbody>
          {d.lines.map((l) => (
            <tr key={l.id}>
              <td>{l.item}</td>
              <td>{l.part_number || "–"}</td>
              <td className="num">{l.quantity}</td>
              <td className="num">{l.received_quantity || ""}</td>
              <td className="write-in" />
            </tr>
          ))}
        </tbody>
      </table>
      {absolute ? (
        <section className="sheet-receive">
          {qr && <img src={qr} alt={`QR code for ${absolute}`} width={180} height={180} />}
          <div className="stack" style={{ gap: 6 }}>
            <strong>When it arrives, mark it received:</strong>
            <span>Scan the QR code, or open</span>
            <span className="code sheet-big">{pageAddress}</span>
            <span>and enter</span>
            <span>
              PO <span className="code sheet-big">{d.label}</span> &nbsp; Code <span className="code sheet-big">{d.receiving_code}</span>
            </span>
          </div>
        </section>
      ) : (
        <p>Hand this sheet back to the service desk when it arrives.</p>
      )}
      <p className="sheet-sign">Received by ______________________ &nbsp; Date __________ &nbsp; Signature ______________________</p>
    </div>
  );
}

/** The public receiving page (/receive): no sign-in. PO number + code (from the receiving sheet,
 * its QR code or the heads-up email), then what arrived. */
export function ReceivePage() {
  const brand = useBranding();
  const params = new URLSearchParams(window.location.search);
  const [poNo, setPoNo] = useState(params.get("po") ?? "");
  const [code, setCode] = useState(params.get("code") ?? "");
  const [po, setPo] = useState<PublicPO | null>(null);
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem("layer8.receiver") ?? "";
    } catch {
      return "";
    }
  });
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const lookup = useMutation({ mutationFn: () => api.receiving.lookup(poNo, code), onSuccess: (p) => (setPo(p), setCounts({}), setDone(null)) });
  const receive = useMutation({
    mutationFn: async () => {
      const lines = Object.entries(counts)
        .filter(([, v]) => v !== "" && Number(v) !== 0)
        .map(([id, v]) => ({ line_id: Number(id), quantity: Number(v) }));
      const after = await api.receiving.receive({ po: poNo, code, name, note, lines });
      if (photo) await api.receiving.photo(poNo, code, name, photo);
      return after;
    },
    onSuccess: (p) => {
      try {
        localStorage.setItem("layer8.receiver", name);
      } catch {
        // Not remembered; nothing else changes.
      }
      setPo(p);
      setCounts({});
      setNote("");
      setPhoto(null);
      setDone(p.status === "Received" ? "Thanks: everything on this order is in. The service desk has been told." : "Thanks: saved, and the service desk has been told. The rest is still to come.");
    },
  });
  // Opened from the QR code or the email: look it up straight away.
  useEffect(() => {
    if (params.get("po") && params.get("code")) lookup.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const error = lookup.error ?? receive.error;

  return (
    <div className="portal receiving-screen">
      <header className="topbar">
        <Brand b={brand} />
        <span className="receiving-topbar-label">Delivery receiving</span>
      </header>
      <main className="portal-main stack receiving-main">
        <WorkspaceTitle title="Mark a delivery received" area="DELIVERY DESK" description="Find the order using its number and receiving code, then record only what arrived." />
        <form
          className="card row-gap receiving-lookup"
          aria-label="Find a delivery"
          style={{ margin: 0, alignItems: "flex-end" }}
          onSubmit={(e) => {
            e.preventDefault();
            lookup.mutate();
          }}
        >
          <label className="field" style={{ flex: 1 }}>
            PO number
            <input required value={poNo} onChange={(e) => setPoNo(e.target.value)} placeholder="e.g. PO-00012" autoCapitalize="characters" />
          </label>
          <label className="field" style={{ flex: 1 }}>
            Code <span className="muted small">(on the receiving sheet)</span>
            <input required value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="e.g. K7M4PX" autoCapitalize="characters" autoComplete="off" />
          </label>
          <button className="btn" type="submit" disabled={lookup.isPending}>
            Find it
          </button>
        </form>
        {error && (
          <p className="error" role="alert">
            {error.message}
          </p>
        )}
        {done && (
          <p className="card note" role="status" style={{ margin: 0 }}>
            {done}
          </p>
        )}
        {po && (
          <form
            className="card stack"
            style={{ margin: 0 }}
            aria-label="What arrived"
            onSubmit={(e) => {
              e.preventDefault();
              receive.mutate();
            }}
          >
            <header className="receiving-order-head">
              <p className="workspace-area">RECORD THIS DELIVERY</p>
              <h2>{po.label} · {po.title}</h2>
              <div className="muted small">
                {[po.vendor, po.expected_date && `expected ${new Date(`${po.expected_date}T00:00`).toLocaleDateString()}`, po.status].filter(Boolean).join(" · ")}
              </div>
            </header>
            {po.lines.map((l) => {
              const due = Math.max(0, l.quantity - l.received_quantity);
              return (
                <div key={l.id} className="row-gap receiving-line">
                  <span className="spacer">
                    {l.item} {l.part_number && <span className="muted small">({l.part_number})</span>}
                    <div className="muted small">
                      {l.received_quantity} of {l.quantity} in{due ? `, ${due} still to come` : ", all in"}
                    </div>
                  </span>
                  {due > 0 && (
                    <>
                      <button className="linkish small" type="button" onClick={() => setCounts({ ...counts, [l.id]: String(due) })}>
                        All {due}
                      </button>
                      <input aria-label={`${l.item}: how many arrived`} type="number" min={0} max={due} step="any" style={{ width: 90 }} value={counts[l.id] ?? ""} onChange={(e) => setCounts({ ...counts, [l.id]: e.target.value })} />
                    </>
                  )}
                </div>
              );
            })}
            <label className="field">
              Your name
              <input required value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              Note <span className="muted small">(optional: damaged box, wrong item…)</span>
              <input value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <label className="field">
              Photo of the packing slip <span className="muted small">(optional)</span>
              <input type="file" accept="image/*,application/pdf" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
            </label>
            <div>
              <button className="btn" type="submit" disabled={receive.isPending || !name.trim() || !Object.values(counts).some((v) => v && Number(v) > 0)}>
                Mark received
              </button>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}
