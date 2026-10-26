// SPDX-License-Identifier: AGPL-3.0-or-later
import { type SystemItem } from "../api/client";

const KIND_LABEL: Record<string, string> = { management: "Management", vip: "VIP / virtual", ilo: "iLO / iDRAC / out-of-band", service: "Service", other: "Other" };
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;

/** A system's vendor support details: what a technician needs to open a case. */
export function VendorSupport({ system }: { system: SystemItem }) {
  const v = system.vendor;
  if (!v) return <span className="muted small">No vendor set.</span>;
  const c = system.contact;
  return (
    <div className="small stack" style={{ gap: 2 }}>
      <div>
        <strong>{v.name}</strong>
        {v.account_number && <span className="muted"> · account {v.account_number}</span>}
      </div>
      {v.support_phone && (
        <div>
          Support: <a href={`tel:${v.support_phone}`}>{v.support_phone}</a>
          {v.support_hours && <span className="muted"> ({v.support_hours})</span>}
        </div>
      )}
      {v.support_email && (
        <div>
          <a href={`mailto:${v.support_email}`}>{v.support_email}</a>
        </div>
      )}
      {v.support_portal && /^https?:\/\//i.test(v.support_portal) && (
        <div>
          <a href={v.support_portal} target="_blank" rel="noreferrer">
            Support portal
          </a>
        </div>
      )}
      {c && (
        <div>
          Contact: {c.name} ({c.role}){c.phone && <> · <a href={`tel:${c.phone}`}>{c.phone}</a></>}
          {c.mobile && <> · <a href={`tel:${c.mobile}`}>{c.mobile}</a></>}
          {c.email && <> · <a href={`mailto:${c.email}`}>{c.email}</a></>}
        </div>
      )}
      {system.addresses.length > 0 && (
        <div className="muted">
          {system.addresses.map((a) => `${a.address} (${kindLabel(a.kind)})`).join(" · ")}
        </div>
      )}
    </div>
  );
}
