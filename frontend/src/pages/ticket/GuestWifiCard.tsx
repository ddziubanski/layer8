// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Ticket } from "../../api/client";

const LENGTHS: [number, string][] = [[240, "4 hours"], [480, "8 hours"], [1440, "1 day"], [4320, "3 days"], [10080, "1 week"]];

/** A UniFi guest Wi-Fi voucher for this ticket; the code is noted on the ticket. */
export function GuestWifiCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const sites = useQuery({ queryKey: ["unifi-sites"], queryFn: api.unifi.sites });
  const [site, setSite] = useState("");
  const [minutes, setMinutes] = useState(480);
  const [guests, setGuests] = useState(1);
  const make = useMutation({
    mutationFn: () => api.unifi.voucher(ticket.id, { source_id: site || sites.data?.[0]?.source_id || "", minutes, guests }),
    onSuccess: onChanged,
  });
  if (!sites.data?.length) return <p className="muted small" style={{ margin: 0 }}>No UniFi sites read yet.</p>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {sites.data.length > 1 && (
        <label className="field">
          Site
          <select value={site || sites.data[0].source_id} onChange={(e) => setSite(e.target.value)}>
            {sites.data.map((s) => <option key={s.source_id} value={s.source_id}>{s.name}</option>)}
          </select>
        </label>
      )}
      <div className="row-gap">
        <label className="field spacer">
          Valid for
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {LENGTHS.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
          </select>
        </label>
        <label className="field" style={{ width: 90 }}>
          Guests
          <input type="number" min={1} max={20} value={guests} onChange={(e) => setGuests(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
        </label>
      </div>
      <div>
        <button className="btn2" type="button" disabled={make.isPending} onClick={() => make.mutate()}>
          {make.isPending ? "Making…" : "Make a guest voucher"}
        </button>
      </div>
      {make.data && (
        <p role="status" style={{ margin: 0 }}>
          Voucher <strong className="mono">{make.data.codes.join(", ")}</strong> on {make.data.site}. It&apos;s noted on this ticket.
        </p>
      )}
      {make.error && <p className="error" role="alert">{make.error.message}</p>}
    </div>
  );
}
