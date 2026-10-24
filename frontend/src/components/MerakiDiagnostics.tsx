import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { api, type MerakiObservation } from "../api/client";
import { when } from "../format";

/** Only offered in the client view; ticket actions require a separate audited flow. */
export function MerakiDiagnostics({ observation: o }: { observation: MerakiObservation }) {
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now);
  const id = useId();
  const client = useQueryClient();
  const key = ["meraki-diagnostics", o.network_id, o.mac, o.observed_at];
  const saved = useQuery({ queryKey: key, queryFn: () => api.meraki.diagnostics(o.network_id, o.mac), enabled: open && !!o.diagnostics_available, retry: false });
  const read = useMutation({ mutationFn: () => api.meraki.readDiagnostics(o.network_id, o.mac), onSuccess: data => client.setQueryData(key, data) });
  useEffect(() => {
    if (!open) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);
  if (!o.diagnostics_available) return null;
  const result = saved.data;
  const data = result?.data;
  const events = data?.events ?? [];
  const wait = result ? Math.max(0, Math.ceil((Date.parse(result.retry_at) - now) / 1000)) : 0;
  return <div className="meraki-diagnostics">
    <button className="btn2 small" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>Network diagnostics</button>
    {open && <section id={id} className="stack" aria-label={`Network diagnostics for ${o.description || o.mac}`}>
      <p className="muted small">Read-only checks for the last-known attachment. No network configuration is changed. Wireless events cover the last 15 minutes; switch status is reported by Dashboard.</p>
      {saved.isPending && <p role="status">Loading saved diagnostics…</p>}
      {(saved.error || read.error) && <p role="alert" className="error">{(read.error || saved.error)?.message}</p>}
      {result?.error && <p role="alert" className="error">{result.error}{data && " The last successful result is shown below."}</p>}
      {!saved.isPending && !data && !result?.error && <p className="muted small">No saved diagnostics. Read when you need a check.</p>}
      <div className="row-gap">
        <button className="btn small" type="button" disabled={saved.isPending || read.isPending || wait > 0} onClick={() => read.mutate()}>Read diagnostics</button>
        {read.isPending && <span role="status">Reading Meraki diagnostics…</span>}
        {wait > 0 && <span className="muted small">Available again in {wait}s</span>}
      </div>
      {result && <p className="muted small">Attempted {when(result.attempted_at)}{result.cached ? " · Saved result" : ""} · Client discovery {when(result.client_observed_at)}</p>}
      {data && <div className="stack small" aria-label="Diagnostic result">
        <p className="muted">Last successful read {when(data.read_at)}. This is the read time; port status has no source timestamp.</p>
        {data.kind === "wired" && data.port && <>
          <strong>{data.switch_serial} · Port {data.port.port_id}</strong>
          <dl className="meraki-diagnostic-values">
            <dt>Status</dt><dd>{data.port.status || "Unknown"}</dd>
            <dt>Enabled</dt><dd>{data.port.enabled == null ? "Unknown" : data.port.enabled ? "Yes" : "No"}</dd>
            <dt>Speed / duplex</dt><dd>{[data.port.speed, data.port.duplex].filter(Boolean).join(" / ") || "Unknown"}</dd>
            <dt>Clients on port</dt><dd>{data.port.client_count ?? "Unknown"}</dd>
            <dt>Uplink</dt><dd>{data.port.is_uplink == null ? "Unknown" : data.port.is_uplink ? "Yes" : "No"}</dd>
          </dl>
          <p className="muted">This is port-level evidence. The port may be shared; it does not prove this client is currently connected.</p>
          {(data.port.errors ?? []).length > 0 && <div><b>Port errors</b><ul>{(data.port.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
          {(data.port.warnings ?? []).length > 0 && <div><b>Port warnings</b><ul>{(data.port.warnings ?? []).map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
        </>}
        {data.kind === "wireless" && <>
          <strong>Wireless connectivity events · last 15 minutes</strong>
          {events.length === 0 && <p>No connectivity events were returned for this window. This does not establish that connectivity is healthy.</p>}
          <ol className="meraki-event-list">{events.map((e, i) => <li key={i}>
            <b>{e.type}{e.subtype ? ` · ${e.subtype}` : ""}</b>{e.severity && ` · ${e.severity}`}
            <div className="muted">Occurred {when(e.occurred_at)}{e.device_serial && ` · ${e.device_serial}`}</div>
            <div>{[e.band && `${e.band} GHz`, e.channel != null && `Channel ${e.channel}`, e.rssi != null && `RSSI ${e.rssi} dBm`, e.duration_ms != null && `${e.duration_ms} ms`].filter(Boolean).join(" · ")}</div>
          </li>)}</ol>
          {data.events_truncated && <p className="muted">Showing one page of up to 50 events. Additional events were not fetched.</p>}
        </>}
      </div>}
    </section>}
  </div>;
}
