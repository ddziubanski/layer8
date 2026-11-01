// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, type Me } from "../api/client";
import { can } from "../permissions";
import { moduleEnabled, moduleForPath } from "../navigation";
import { TicketPreview } from "./TicketPreview";

// A page, or a ticket (shown with a preview while highlighted).
type Command = { label: string; to: string; keywords?: string; ticket?: number; detail?: string; group?: string };

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => { const t = setTimeout(() => setSettled(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return settled;
}
export function commandsFor(me: Me): Command[] {
  const commands: Command[] = [
    { label: "Ticket queue", to: "/", keywords: "tickets service desk" },
    { label: "Unassigned tickets", to: "/?view=unassigned" },
    { label: "Tickets at SLA risk", to: "/?view=sla_at_risk" },
    { label: "Dashboard", to: "/dashboard", keywords: "workload overview" },
    { label: "Devices", to: "/devices", keywords: "assets computers" },
    { label: "Changes", to: "/changes" },
    { label: "Problems", to: "/problems", keywords: "investigations known errors incidents" },
    { label: "Purchase orders", to: "/purchase-orders" },
    { label: "Vendors", to: "/vendors" },
    { label: "IP addresses", to: "/ipam", keywords: "network subnets ipam" },
    { label: "Knowledge base", to: "/kb", keywords: "help articles" },
    { label: "Scripts", to: "/scripts" },
    { label: "Reports", to: "/reports" },
    { label: "My preferences", to: "/preferences" },
    { label: "Self-service portal", to: "/portal" },
  ];
  if (me.role === "admin") commands.push({ label: "Settings", to: "/settings" }, { label: "Manage users", to: "/settings/users" });
  if (me.role === "admin" || can(me, "rules_manage")) commands.push({ label: "Ticket rules", to: "/settings/rules" });
  return commands.filter(command => {
    const module = moduleForPath(command.to);
    return !module || moduleEnabled(me, module);
  });
}

/** Navigation only: every destination still uses its existing API authorization. */
export function CommandPalette({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const navigate = useNavigate();
  const id = useId();
  const available = commandsFor(me);
  const staff = me.role !== "requester" && !me.portal_only;
  const needle = query.trim().toLowerCase();
  const matches = available.filter(c => `${c.label} ${c.keywords ?? ""}`.toLowerCase().includes(needle));
  const number = /^(?:#|ticket\s*)?(\d+)$/i.exec(query.trim());
  const ticketNumber = number && Number.isSafeInteger(Number(number[1])) && Number(number[1]) > 0 ? Number(number[1]) : null;
  if (ticketNumber) matches.unshift({ label: `Open ticket #${ticketNumber}`, to: `/tickets/${ticketNumber}`, ticket: staff ? ticketNumber : undefined });
  // Quick lookup: the best few tickets for the words typed so far (any status), each with a preview.
  const words = useDebounced(open && staff && !ticketNumber && needle.length >= 3 ? query.trim() : "", 250);
  const found = useQuery({ queryKey: ["palette-tickets", words], queryFn: () => api.tickets({ scope: "all", q: words, limit: 6 }), enabled: !!words, staleTime: 15_000, retry: false });
  if (words && words.toLowerCase() === needle) {
    for (const t of found.data?.items ?? []) matches.push({ label: `${t.label} ${t.subject}`, to: `/tickets/${t.id}`, ticket: t.id, detail: t.status.replace("_", " ") });
  }
  // Everything else (devices by name, IP, MAC or serial; people; problems; changes; articles; POs; vendors; systems).
  const term = useDebounced(open && staff && needle.length >= 2 ? query.trim() : "", 250);
  const everything = useQuery({ queryKey: ["palette-search", term], queryFn: () => api.search(term), enabled: !!term, staleTime: 15_000, retry: false });
  if (term && term.toLowerCase() === needle) {
    for (const g of everything.data?.groups ?? []) for (const h of g.hits) matches.push({ label: h.label, to: h.to, detail: h.detail || undefined, group: g.label });
  }
  // Staff can always search ticket text (subject, description, notes), closed history included.
  if (staff && needle.length >= 2) matches.push({ label: `Search tickets for “${query.trim()}”`, to: `/?scope=all&q=${encodeURIComponent(query.trim())}` });
  const selected = Math.min(active, Math.max(0, matches.length - 1));
  const previewId = useDebounced(open ? matches[selected]?.ticket ?? null : null, 150);
  const show = () => { returnTo.current = document.activeElement as HTMLElement | null; setQuery(""); setActive(0); setOpen(true); };
  const close = () => setOpen(false);
  const choose = (command: Command) => { close(); navigate(command.to); };

  useEffect(() => {
    if (open) document.getElementById(`${id}-result-${selected}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, selected, query, id]);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    return () => { if (returnTo.current?.isConnected) returnTo.current.focus(); };
  }, [open]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.isComposing || event.repeat) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        if (!open && document.querySelector('[aria-modal="true"], dialog[open]')) return;
        event.preventDefault();
        if (open) close(); else show();
      } else if (open && event.key === "Escape") { event.preventDefault(); close(); }
      else if (open && event.key === "Tab") {
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button, input, a[href], [tabindex="0"]') ?? []);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    const guard = (event: FocusEvent) => { if (open && event.target instanceof Node && !dialog.current?.contains(event.target)) input.current?.focus(); };
    document.addEventListener("focusin", guard);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("focusin", guard); };
  }, [open]);

  return <>
    <button className="btn2 command-trigger" type="button" onClick={show} aria-keyshortcuts="Control+K Meta+K">Find a page or ticket <kbd>Ctrl K</kbd></button>
    {open && <div className="overlay command-overlay" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}>
      <div className={`command-palette${previewId ? " with-preview" : ""}`} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} ref={dialog}>
        <div className="command-main">
        <div className="row-gap"><h2 id={`${id}-title`} className="spacer">Go anywhere</h2><button type="button" className="linkish" onClick={close}>Close</button></div>
        <label className="field">Find a page, ticket, device, person or anything else
          <input ref={input} role="combobox" aria-expanded={matches.length > 0} aria-autocomplete="list" aria-controls={`${id}-results`} aria-activedescendant={matches.length ? `${id}-result-${selected}` : undefined}
            value={query} onChange={e => { setQuery(e.target.value); setActive(0); }} placeholder="Try #4821, printer offline, 10.20.0.5 or a name"
            onKeyDown={e => {
              if (e.nativeEvent.isComposing) return;
              if (matches.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); setActive((selected + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length); }
              else if (e.key === "Enter" && matches.length) { e.preventDefault(); choose(matches[selected]); }
            }} />
        </label>
        <ul id={`${id}-results`} role="listbox" aria-label="Destinations" className="command-results">
          {matches.map((command, i) => <Fragment key={`${i}:${command.to}`}>
          {command.group && command.group !== matches[i - 1]?.group && <li role="presentation" className="command-group muted small">{command.group}</li>}
          <li id={`${id}-result-${i}`} role="option" aria-selected={i === selected} onMouseDown={e => e.preventDefault()}
            onMouseEnter={() => command.ticket && setActive(i)} onClick={() => choose(command)}
            className={command.ticket && !command.label.startsWith("Open ticket") ? "command-ticket" : undefined}>
            <span className="command-label">{command.label}</span>{command.detail ? <span className="muted small">{command.detail}</span> : <span aria-hidden="true">↗</span>}
          </li></Fragment>)}
        </ul>
        {((words && found.isFetching) || (term && everything.isFetching)) && <p role="status" className="muted small" style={{ margin: 0 }}>Searching…</p>}
        {!matches.length && <p role="status" className="muted">No matching destination.</p>}
        <p className="muted small">↑ ↓ to browse and preview tickets · Enter to open · Esc to close · Ctrl K / ⌘ K</p>
        </div>
        {previewId && <TicketPreview key={previewId} id={previewId} />}
      </div>
    </div>}
  </>;
}
