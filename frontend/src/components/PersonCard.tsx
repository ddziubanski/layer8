// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router";

import { api, type Person } from "../api/client";
import { plural } from "../format";

const PRESENCE: Record<string, string> = { available: "Available", busy: "Busy", away: "Away", offline: "Offline" };

/** Title, department and contact lines, shared by the pop-up card and the person page. */
export function PersonDetails({ p }: { p: Person }) {
  return (
    <>
      {(p.title || p.department) && <p className="muted">{[p.title, p.department].filter(Boolean).join(" · ")}</p>}
      <dl className="person-facts">
        <dt>Email</dt>
        {/* Imported addresses can be odd ("SMTP:{…}…"); only a plain address becomes a link. */}
        <dd>{/^[^\s@{}:]+@[^\s@{}]+\.[^\s@{}]+$/.test(p.email) ? <a href={`mailto:${p.email}`}>{p.email}</a> : p.email}</dd>
        {p.phone && <><dt>Phone</dt><dd><a href={`tel:${p.phone.replace(/[^\d+]/g, "")}`}>{p.phone}</a></dd></>}
        {p.mobile && <><dt>Mobile</dt><dd><a href={`tel:${p.mobile.replace(/[^\d+]/g, "")}`}>{p.mobile}</a></dd></>}
        {p.office && <><dt>Office</dt><dd>{p.office}</dd></>}
        {p.company && <><dt>Company</dt><dd>{p.company}</dd></>}
        {p.manager && <><dt>Manager</dt><dd><Link to={`/people/${p.manager.id}`}>{p.manager.display_name}</Link>{p.manager.title && <span className="muted"> · {p.manager.title}</span>}</dd></>}
        {p.presence && <><dt>Teams</dt><dd>{PRESENCE[p.presence] ?? p.presence}</dd></>}
        {(p.upn || p.sam_account_name) && <><dt>Signs in as</dt><dd>{p.upn || p.sam_account_name}</dd></>}
        <dt>Account</dt>
        <dd>
          {p.in_directory ? "Directory (AD)" : p.auth_source === "local" ? "Local" : "Directory"}
          {p.role !== "requester" && ` · ${p.role === "admin" ? "Administrator" : "Technician"}`}
          {!p.is_active && <span className="chip warn person-inactive">Inactive</span>}
        </dd>
      </dl>
    </>
  );
}

/** A person's name that opens their contact card instead of whatever row it sits in. */
export function PersonLink({ id, name }: { id: number; name: string }) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<CSSProperties>({});
  const button = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const person = useQuery({ queryKey: ["person", id], queryFn: () => api.person(id), enabled: open, staleTime: 60_000 });

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !card.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) {
        setOpen(false);
        if (e instanceof KeyboardEvent) button.current?.focus();
      }
    };
    document.addEventListener("keydown", close);
    document.addEventListener("mousedown", close);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    const r = button.current!.getBoundingClientRect();
    // Below the name, or above it near the bottom of the window; never off the right edge.
    const below = r.bottom + 300 < window.innerHeight;
    setPlace({ left: Math.max(8, Math.min(r.left, window.innerWidth - 348)), ...(below ? { top: r.bottom + 6 } : { bottom: window.innerHeight - r.top + 6 }) });
    setOpen(!open);
  }

  const p = person.data;
  return (
    <span className="person-anchor" onClick={(e) => e.stopPropagation()}>
      <button ref={button} type="button" className="linkish person-link" aria-expanded={open} aria-haspopup="dialog" onClick={toggle}>
        {name}
      </button>
      {open && (
        <div ref={card} className="person-card" role="dialog" aria-label={`${name}: contact card`} style={place}>
          <strong className="person-name">{name}</strong>
          {person.isPending && <p className="muted small">Loading…</p>}
          {person.error && <p className="error small">{person.error.message}</p>}
          {p && (
            <>
              <PersonDetails p={p} />
              <p className="small">
                {plural(p.open_tickets, "open ticket")} · {plural(p.all_tickets, "ticket")} in all
              </p>
              <Link className="btn2 person-more" to={`/people/${p.id}`} onClick={() => setOpen(false)}>
                View profile
              </Link>
            </>
          )}
        </div>
      )}
    </span>
  );
}
