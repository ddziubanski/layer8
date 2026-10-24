// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Layout, type Me } from "../../api/client";
import type { Panel } from "../../components/Arrange";
import { ME_KEY } from "../../queryClient";

export type TicketWorkspacePreference = NonNullable<Me["ticket_workspace"]>;
export type TicketTool = TicketWorkspacePreference["selected_tool"];
export const DEFAULT_TICKET_WORKSPACE: TicketWorkspacePreference = { mode: "drawer", selected_tool: "details", keep_open: false, width: 440 };
export const TICKET_TOOLS: { id: TicketTool; label: string; symbol: string; panels: string[] }[] = [
  { id: "details", label: "Details", symbol: "◧", panels: ["status", "assignment", "requester", "actions"] },
  { id: "devices", label: "Devices", symbol: "▣", panels: ["computer", "devices", "systems", "vms"] },
  { id: "directory", label: "Directory", symbol: "♙", panels: ["ad_account"] },
  { id: "scripts", label: "Scripts", symbol: "⌘", panels: ["scripts"] },
  { id: "network", label: "Network", symbol: "⌗", panels: ["network"] },
  { id: "copilot", label: "Copilot", symbol: "✧", panels: ["copilot"] },
  { id: "knowledge", label: "Knowledge", symbol: "▧", panels: ["knowledge"] },
  { id: "files", label: "Files", symbol: "⌕", panels: ["attachments"] },
  { id: "guest_wifi", label: "Guest Wi-Fi", symbol: "⌁", panels: ["guest_wifi"] },
];
const SECTIONS = [
  { id: "conversation", label: "Conversation", panels: ["conversation"] },
  { id: "work", label: "Work", panels: ["children", "checklists", "expenses", "time", "changes"] },
  { id: "history", label: "History", panels: ["history"] },
];

function tabKey(event: KeyboardEvent<HTMLButtonElement>, ids: string[], active: string, choose: (id: string) => void, prefix: string) {
  const index = ids.indexOf(active);
  const next = event.key === "Home" ? 0 : event.key === "End" ? ids.length - 1 : event.key === "ArrowRight" ? (index + 1) % ids.length : event.key === "ArrowLeft" ? (index - 1 + ids.length) % ids.length : -1;
  if (next < 0) return;
  event.preventDefault(); choose(ids[next]); document.getElementById(`${prefix}-${ids[next]}`)?.focus();
}

/** Activated panels stay mounted while hidden, preserving drafts/files and in-flight operations. */
export function TicketToolsWorkspace({ me, panels, layout, request }: {
  me: Me; panels: Record<string, Panel>; layout: Layout; request: { tool: TicketTool; sequence: number } | null;
}) {
  const queryClient = useQueryClient();
  const [prefs, setPrefs] = useState<TicketWorkspacePreference>(me.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE);
  const [open, setOpen] = useState(prefs.keep_open);
  const [section, setSection] = useState("conversation");
  const [visited, setVisited] = useState<Set<string>>(() => new Set(["conversation", ...(prefs.keep_open ? [prefs.selected_tool] : [])]));
  const [width, setWidth] = useState(prefs.width);
  const [saving, setSaving] = useState(0);
  const [error, setError] = useState("");
  const queue = useRef(Promise.resolve());
  const latest = useRef(prefs);
  const toggle = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLElement>(null);
  const previousOpen = useRef(open);
  const prefix = useId();
  const hidden = new Set(layout.hidden ?? []);
  const visible = (id: string) => id === "conversation" || (!hidden.has(id) && panels[id]?.node != null);
  const tools = TICKET_TOOLS.filter(tool => tool.panels.some(visible));
  const selected = tools.some(t => t.id === prefs.selected_tool) ? prefs.selected_tool : tools[0]?.id;
  const sections = SECTIONS.filter(item => item.panels.some(visible));
  const activeSection = sections.some(item => item.id === section) ? section : "conversation";

  function save(next: TicketWorkspacePreference) {
    latest.current = next; setPrefs(next); setError(""); setSaving(n => n + 1);
    // Serialize rapid tool/width choices: a slow older request cannot replace a newer preference.
    const work = queue.current.catch(() => {}).then(() => api.savePreferences({ ticket_workspace: next }));
    queue.current = work.then(() => {}, () => {});
    void work.then(profile => {
      queryClient.setQueryData<Me>(ME_KEY, current => current?.id === me.id ? { ...current, ticket_workspace: profile.ticket_workspace } : current);
    }, failure => setError(failure.message)).finally(() => setSaving(n => n - 1));
  }
  function chooseTool(id: TicketTool, persist = true) {
    setOpen(true); setVisited(old => new Set([...old, id]));
    const next = { ...latest.current, selected_tool: id };
    if (persist) save(next); else { latest.current = next; setPrefs(next); }
  }
  useEffect(() => {
    if (request) chooseTool(request.tool);
  }, [request?.sequence]); // A header/remote-action shortcut deliberately reopens the same tool.
  useEffect(() => {
    if (open && selected) setVisited(old => old.has(selected) ? old : new Set([...old, selected]));
    if (open && !previousOpen.current) document.getElementById(`${prefix}-tool-${selected}`)?.focus();
    previousOpen.current = open;
  }, [open, selected, prefix]);
  useEffect(() => {
    if (request && open && selected === request.tool) document.getElementById(`${prefix}-tool-${selected}`)?.focus();
  }, [request?.sequence, open, selected, prefix]);
  function close() { setOpen(false); toggle.current?.focus(); }
  function chooseSection(id: string) { setSection(id); setVisited(old => new Set([...old, id])); }
  function saveWidth() { if (width !== latest.current.width) save({ ...latest.current, width }); }

  const render = (ids: string[]) => ids.filter(visible).map(id => <div key={id} data-ticket-panel={id}>{panels[id].node}</div>);
  return <div className="ticket-tools-workspace" style={{ "--ticket-drawer-width": `${width}px` } as CSSProperties}>
    <div className="ticket-section-bar">
      <div className="ticket-section-tabs" role="tablist" aria-label="Ticket sections">
        {sections.map(item => <button key={item.id} id={`${prefix}-section-${item.id}`} role="tab" type="button"
          aria-selected={activeSection === item.id} aria-controls={`${prefix}-section-panel-${item.id}`} tabIndex={activeSection === item.id ? 0 : -1}
          onKeyDown={event => tabKey(event, sections.map(s => s.id), activeSection, chooseSection, `${prefix}-section`)} onClick={() => chooseSection(item.id)}>{item.label}</button>)}
      </div>
      <button ref={toggle} className={open ? "btn" : "btn2"} type="button" aria-expanded={open} aria-controls={`${prefix}-drawer`} onClick={() => { if (open) close(); else setOpen(true); }}>▥ Tools</button>
    </div>
    <div className={`ticket-workspace-columns${open && tools.length ? " drawer-open" : ""}`}>
      <div className="ticket-primary-panels">
        {sections.map(item => <section key={item.id} id={`${prefix}-section-panel-${item.id}`} role="tabpanel" aria-labelledby={`${prefix}-section-${item.id}`} hidden={activeSection !== item.id} tabIndex={0}>
          {visited.has(item.id) && <div className="stack">{render(item.panels)}</div>}
        </section>)}
      </div>
      <aside ref={drawer} id={`${prefix}-drawer`} className="ticket-tool-drawer" aria-label="Ticket tools" hidden={!open || !tools.length}
        onKeyDown={event => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); close(); } }}>
        <header className="ticket-tool-drawer-head"><h2>Ticket tools</h2><button className="btn2" type="button" aria-label="Close ticket tools" onClick={close}>×</button></header>
        <div className="ticket-tool-tabs" role="tablist" aria-label="Ticket tools">
          {tools.map(tool => <button key={tool.id} id={`${prefix}-tool-${tool.id}`} role="tab" type="button" aria-selected={selected === tool.id}
            aria-controls={`${prefix}-tool-panel-${tool.id}`} tabIndex={selected === tool.id ? 0 : -1}
            onKeyDown={event => tabKey(event, tools.map(t => t.id), selected!, id => chooseTool(id as TicketTool), `${prefix}-tool`)} onClick={() => chooseTool(tool.id)}>
            <span aria-hidden="true">{tool.symbol}</span>{tool.label}
          </button>)}
        </div>
        <div className="ticket-tool-body">
          {tools.map(tool => <section key={tool.id} id={`${prefix}-tool-panel-${tool.id}`} role="tabpanel" aria-labelledby={`${prefix}-tool-${tool.id}`} hidden={selected !== tool.id} tabIndex={0}>
            {visited.has(tool.id) && <div className="stack">{render(tool.panels)}</div>}
          </section>)}
        </div>
        <footer className="ticket-tool-footer">
          <label className="inline small"><input type="checkbox" checked={prefs.keep_open} onChange={event => save({ ...latest.current, keep_open: event.target.checked })} />Keep panel open on other tickets</label>
          <label className="field small ticket-tool-width">Panel width <span>{width}px</span><input aria-label="Tools drawer width" type="range" min={320} max={720} step={20} value={width}
            onChange={event => setWidth(Number(event.target.value))} onPointerUp={saveWidth} onKeyUp={saveWidth} onBlur={saveWidth} /></label>
          {saving > 0 && <span className="muted small" role="status">Saving workspace…</span>}
          {error && <p className="error small" role="alert">Workspace preference was not saved: {error} <button className="linkish" type="button" onClick={() => save(latest.current)}>Retry</button></p>}
        </footer>
      </aside>
    </div>
  </div>;
}
