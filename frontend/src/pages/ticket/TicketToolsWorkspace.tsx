// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Layout, type Me } from "../../api/client";
import type { Panel } from "../../components/Arrange";
import { ME_KEY } from "../../queryClient";
export type TicketWorkspacePreference = NonNullable<Me["ticket_workspace"]>;
export type TicketTool = TicketWorkspacePreference["selected_tool"];
// Legacy API fields remain readable; all technicians now use Ticket Focus.
export const DEFAULT_TICKET_WORKSPACE: TicketWorkspacePreference = { mode: "drawer", selected_tool: "details", keep_open: false, width: 440 };
export const TICKET_TOOLS: { id: TicketTool; label: string; symbol: string; panels: string[] }[] = [
  { id: "details", label: "Details", symbol: "◧", panels: ["status", "assignment", "requester", "actions"] },
  { id: "devices", label: "Devices", symbol: "▣", panels: ["computer", "devices", "systems", "vms"] },
  { id: "directory", label: "Directory", symbol: "♙", panels: ["ad_account"] },
  { id: "scripts", label: "Scripts", symbol: "⌘", panels: ["scripts"] },
  { id: "network", label: "Network", symbol: "⌗", panels: ["network"] },
  { id: "copilot", label: "Copilot", symbol: "✧", panels: ["copilot"] },
  { id: "knowledge", label: "Library", symbol: "▧", panels: ["knowledge"] },
  { id: "files", label: "Files", symbol: "⌕", panels: ["attachments"] },
  { id: "guest_wifi", label: "Guest Wi-Fi", symbol: "⌁", panels: ["guest_wifi"] },
];
const SECTIONS = [
  { id: "conversation", label: "Conversation", panels: ["conversation"] },
  { id: "details", label: "Details", panels: ["status", "assignment", "requester", "actions"] },
  { id: "work", label: "Work", panels: ["children", "related", "checklists", "expenses", "time", "changes"] },
  { id: "history", label: "History", panels: ["history"] },
];

/** Lazily mounted tabs remain mounted while hidden, preserving drafts and running actions. */
export function TicketToolsWorkspace({ me, panels, layout, request, context }: {
  me: Me; panels: Record<string, Panel>; layout: Layout; request: { tool: TicketTool; sequence: number } | null; context?: ReactNode;
}) {
  const client = useQueryClient();
  const prefix = useId();
  const [section, setSection] = useState("conversation");
  const [tool, setTool] = useState<TicketTool>(me.ticket_workspace?.selected_tool ?? "devices");
  const [visited, setVisited] = useState<Set<string>>(() => new Set(["conversation"]));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(0);
  const queue = useRef(Promise.resolve());
  const latest = useRef(me.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE);
  const hidden = new Set(layout.hidden ?? []);
  const visible = (id: string) => id === "conversation" || (!hidden.has(id) && panels[id]?.node != null);
  const tools = TICKET_TOOLS.filter(item => item.id !== "details" && item.panels.some(visible));
  const selected = tools.some(item => item.id === tool) ? tool : tools[0]?.id;
  const sections = [...SECTIONS.filter(item => item.panels.some(visible)), ...(tools.length ? [{ id: "tools", label: "Tools", panels: [] }] : [])];
  const active = sections.some(item => item.id === section) ? section : "conversation";
  function save(next: TicketWorkspacePreference) {
    latest.current = next; setError(""); setSaving(n => n + 1);
    const work = queue.current.catch(() => {}).then(() => api.savePreferences({ ticket_workspace: next }));
    queue.current = work.then(() => {}, () => {});
    void work.then(profile => client.setQueryData<Me>(ME_KEY, current => current?.id === me.id ? { ...current, ticket_workspace: profile.ticket_workspace } : current), failure => setError(failure.message)).finally(() => setSaving(n => n - 1));
  }
  function chooseSection(id: string) { setSection(id); setVisited(old => new Set([...old, id])); }
  function chooseTool(id: TicketTool) { setTool(id); setVisited(old => new Set([...old, `tool-${id}`])); save({ ...latest.current, selected_tool: id }); }
  useEffect(() => {
    if (active === "tools" && selected) setVisited(old => old.has(`tool-${selected}`) ? old : new Set([...old, `tool-${selected}`]));
  }, [active, selected]);
  useEffect(() => {
    if (!request) return;
    if (request.tool === "details") chooseSection("details");
    else { chooseSection("tools"); chooseTool(request.tool); }
  }, [request?.sequence]);
  const render = (ids: string[]) => ids.filter(visible).map(id => <div key={id} data-ticket-panel={id}>{panels[id].node}</div>);
  return <div className="ticket-focus-workspace">
    <div className="ticket-focus-main">
      <div className="ticket-section-tabs" role="tablist" aria-label="Ticket sections">
        {sections.map(item => <button key={item.id} id={`${prefix}-tab-${item.id}`} role="tab" type="button" aria-selected={active === item.id} aria-controls={`${prefix}-panel-${item.id}`} tabIndex={active === item.id ? 0 : -1}
          onKeyDown={event => {
            const index = sections.findIndex(s => s.id === active);
            const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 : event.key === "ArrowRight" ? (index + 1) % sections.length : event.key === "ArrowLeft" ? (index - 1 + sections.length) % sections.length : -1;
            if (next < 0) return;
            event.preventDefault(); chooseSection(sections[next].id); document.getElementById(`${prefix}-tab-${sections[next].id}`)?.focus();
          }} onClick={() => chooseSection(item.id)}>{item.label}</button>)}
      </div>
      {sections.map(item => <section key={item.id} id={`${prefix}-panel-${item.id}`} role="tabpanel" aria-labelledby={`${prefix}-tab-${item.id}`} hidden={active !== item.id}>
        {item.id !== "tools" ? visited.has(item.id) && <div className="stack">{render(item.panels)}</div> : visited.has("tools") && <>
          <div className="ticket-focus-tools" role="group" aria-label="Ticket tools">{tools.map(item => <button key={item.id} type="button" className="btn2" aria-pressed={selected === item.id} onClick={() => chooseTool(item.id)}><span aria-hidden="true">{item.symbol}</span>{item.label}</button>)}</div>
          {tools.map(item => <section key={item.id} aria-label={item.label} hidden={selected !== item.id}>{visited.has(`tool-${item.id}`) && <div className="stack">{render(item.panels)}</div>}</section>)}
        </>}
      </section>)}
      {saving > 0 && <span className="small muted" role="status">Saving workspace…</span>}
      {error && <p className="error small" role="alert">Workspace preference was not saved: {error} <button className="linkish" type="button" onClick={() => save(latest.current)}>Retry</button></p>}
    </div>
    {context && <aside className="ticket-focus-context" aria-label="At a glance">{context}<hr /><h2>Quick tools</h2><div className="stack">{[{ id: "details" as TicketTool, label: "Status & assignment" }, ...tools.filter(item => ["copilot", "knowledge", "devices", "scripts"].includes(item.id))].filter(item => item.id !== "details" || sections.some(s => s.id === "details")).map(item => <button className="btn2" key={item.id} type="button" onClick={() => { if (item.id === "details") chooseSection("details"); else { chooseSection("tools"); chooseTool(item.id); } }}>{item.label}</button>)}</div></aside>}
  </div>;
}
