// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api, type Layout, type User } from "../api/client";

import { widgetInfo } from "./widgetCatalog";

export type Panel = { label: string; node: ReactNode };
export type FieldLayout = { order?: string[]; hidden?: string[]; wide?: string[] };
/** A panel's arrangeable fields: group id -> the panel it's in, a title, and each field's label. */
export type FieldGroups = Record<string, { panel: string; title: string; labels: Record<string, string> }>;

/** The fields of a group in order, without hidden ones, with whether each is full width. */
export function arrangedFields(layout: { fields?: Record<string, FieldLayout> } | undefined, group: string, usual: string[], usualWide: string[] = []) {
  const g = layout?.fields?.[group];
  const given = g?.order ?? [];
  const order = g ? [...given, ...usual.filter((f) => !given.includes(f))] : usual;
  const hidden = new Set(g?.hidden ?? []);
  const wide = new Set(g ? (g.wide ?? []) : usualWide);
  return order.filter((f) => !hidden.has(f)).map((f) => ({ id: f, wide: wide.has(f) }));
}
type Zone = "top" | "main" | "side" | "bottom";
const ZONES: [Zone, string][] = [
  ["top", "Across the top"],
  ["main", "Main column"],
  ["side", "Side column"],
  ["bottom", "Across the bottom"],
];

/** A page made of panels in two columns, with full-width rows above and below, laid out by
 * app.layouts: everyone's default (admins) or the technician's own. "Arrange" turns on drag and
 * drop (with buttons and a menu for the keyboard). */
export function useArrangement(page: string) {
  const layout = useQuery({ queryKey: ["layout", page], queryFn: () => api.layouts.get(page), staleTime: 5 * 60_000 });
  const [editing, setEditing] = useState(false);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (!editing && restoreFocus.current) {
      document.querySelector<HTMLElement>(`[data-arrange-page="${page}"]`)?.focus();
      restoreFocus.current = false;
    }
  }, [editing, page]);
  return { layout: layout.data, editing, setEditing: (value: boolean) => { if (value) restoreFocus.current = true; setEditing(value); } };
}

function move(l: Layout, id: string, to: Zone | "hidden", before: string | null): Layout {
  const drop = (list: string[] | undefined) => (list ?? []).filter((p) => p !== id);
  const out: Layout = { ...l, top: drop(l.top), main: drop(l.main), side: drop(l.side), bottom: drop(l.bottom), hidden: drop(l.hidden) };
  const list = out[to];
  const i = before ? list.indexOf(before) : -1;
  list.splice(i < 0 ? list.length : i, 0, id);
  return out;
}

export function ArrangedColumns({
  page,
  panels,
  fieldGroups = {},
  me,
  editing,
  onDone,
  layout,
  required,
  hasMine,
  editorOnly = false,
}: {
  page: string;
  // Or made from the layout being arranged, so field changes show as they're made.
  panels: Record<string, Panel> | ((shown: Layout) => Record<string, Panel>);
  fieldGroups?: FieldGroups;
  me: User;
  editing: boolean;
  onDone: () => void;
  layout: Layout;
  required: string[];
  hasMine: boolean;
  // Drawer mode keeps its existing live workspace mounted separately.
  editorOnly?: boolean;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Layout | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [dragging, setDragging] = useState<string | null>(null);
  const shown = editing ? (draft ?? layout) : layout;
  const built = typeof panels === "function" ? panels(shown) : panels;
  const finish = (data: unknown) => {
    queryClient.setQueryData(["layout", page], data);
    setDraft(null);
    onDone();
  };
  const saveMine = useMutation({ mutationFn: () => api.layouts.saveMine(page, { ...shown, widget_mode: true }), onSuccess: finish });
  const saveDefault = useMutation({ mutationFn: () => api.layouts.saveDefault(page, { ...shown, widget_mode: true }), onSuccess: finish });
  const resetMine = useMutation({ mutationFn: () => api.layouts.resetMine(page), onSuccess: finish });
  const busy = saveMine.isPending || saveDefault.isPending || resetMine.isPending;
  useEffect(() => {
    if (!editing) { setDraft(null); setDragging(null); setSearch(""); setCategory("all"); saveMine.reset(); saveDefault.reset(); resetMine.reset(); }
  }, [editing]);
  const error = saveMine.error ?? saveDefault.error ?? resetMine.error;
  // From the latest draft, so quick changes in a row all count.
  const available = (id: string) => built[id]?.node != null && built[id]?.node !== false;
  const change = (id: string, to: Zone | "hidden", before: string | null) => {
    if (busy || !built[id] || (to === "hidden" && required.includes(id))) return;
    // A hidden, unavailable widget cannot be activated by a drag or keyboard shortcut.
    if (to !== "hidden" && (shown.hidden ?? []).includes(id) && !available(id)) return;
    setDraft(d => move(d ?? layout, id, to, before));
  };
  const essentials = () => setDraft(d => {
    const next = d ?? layout;
    return { ...next, ...Object.fromEntries(ZONES.map(([zone]) => [zone, (next[zone] ?? []).filter(id => required.includes(id))])),
      hidden: [...new Set([...(next.hidden ?? []), ...ZONES.flatMap(([zone]) => next[zone] ?? [])])].filter(id => !required.includes(id)) };
  });
  const catalog = Object.entries(built).filter(([id]) => ZONES.some(([zone]) => (shown[zone] ?? []).includes(id)) || (shown.hidden ?? []).includes(id));
  const categories = [...new Set(catalog.map(([id]) => widgetInfo(page, id).category))];
  const needle = search.trim().toLowerCase();
  const matches = catalog.filter(([id, panel]) => {
    const info = widgetInfo(page, id);
    return (category === "all" || info.category === category) && `${panel.label} ${info.description} ${info.category}`.toLowerCase().includes(needle);
  });

  const frame = (id: string, col: Zone) => {
    const p = built[id];
    if (!p) return null;
    if (!editing) return <div key={id}>{p.node}</div>;
    const list = shown[col] ?? [];
    const i = list.indexOf(id);
    return (
      <div
        key={id}
        className={`arrange-frame${dragging === id ? " dragging" : ""}`}
        draggable
        onDragStart={(e) => (e.dataTransfer.setData("text/plain", id), setDragging(id))}
        onDragEnd={() => setDragging(null)}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const from = e.dataTransfer.getData("text/plain");
          if (from && from !== id) change(from, col, id);
        }}
        aria-label={`Panel: ${p.label}`}
        role="group"
      >
        <div className="arrange-bar">
          <span className="arrange-grip" aria-hidden="true">
            ⠿
          </span>
          <strong className="spacer">{p.label}</strong>
          <button type="button" className="linkish small" disabled={i === 0} onClick={() => change(id, col, list[i - 1])} aria-label={`Move ${p.label} up`}>
            Up
          </button>
          <button type="button" className="linkish small" disabled={i === list.length - 1} onClick={() => change(id, col, list[i + 2] ?? null)} aria-label={`Move ${p.label} down`}>
            Down
          </button>
          <select className="arrange-where" value={col} onChange={(e) => change(id, e.target.value as Zone, null)} aria-label={`Where ${p.label} goes`}>
            {ZONES.map(([z, label]) => (
              <option key={z} value={z}>
                {label}
              </option>
            ))}
          </select>
          {!required.includes(id) && (
            <button type="button" className="linkish small" onClick={() => change(id, "hidden", null)} aria-label={`Hide ${p.label}`}>
              Hide
            </button>
          )}
        </div>
        {Object.entries(fieldGroups)
          .filter(([, g]) => g.panel === id)
          .map(([group, g]) => (
            <FieldsEditor key={group} title={g.title} labels={g.labels} value={shown.fields?.[group]} onChange={(v) => setDraft((d) => ({ ...(d ?? layout), fields: { ...(d ?? layout).fields, [group]: v } }))} />
          ))}
        <div className="arrange-body widget-placeholder"><p>{widgetInfo(page, id).description}</p><span className="muted small">{required.includes(id) ? "Required widget" : available(id) ? "Widget selected. Live tools stay in your workspace while you arrange." : "Currently unavailable; it will appear when applicable and permitted."}</span></div>
      </div>
    );
  };
  const arrangeBar = editing && (
    <div className="card note stack" style={{ margin: 0, gap: 8 }} role="region" aria-label="Arrange this page">
      <strong>Arrange this page</strong>
      {editorOnly && <span className="muted small">This ticket uses Conversation / Work and the tools drawer. Widget choices apply here; column positions apply in Widget canvas mode, selected in My preferences for the next ticket.</span>}
      <span className="small">Build your workspace from widgets. Add only what you need, then drag or use Up, Down and the placement menu. This editor does not load live tools or enable integrations. Changes apply when saved.</span>
      <div className="row-gap"><strong>Widget builder</strong><span className="muted small">{catalog.filter(([id]) => !(shown.hidden ?? []).includes(id)).length} selected · {(shown.hidden ?? []).length} hidden</span><button type="button" className="btn2" onClick={essentials}>Start with essentials</button></div>
      <div className="row-gap">
        <button className="btn" type="button" onClick={() => saveMine.mutate()}>
          Save for me
        </button>
        {me.role === "admin" && (
          <button className="btn2" type="button" onClick={() => saveDefault.mutate()}>
            Save as everyone's default
          </button>
        )}
        {hasMine && (
          <button className="btn2" type="button" onClick={() => resetMine.mutate()}>
            Use the default again
          </button>
        )}
        <button className="linkish small" type="button" onClick={() => (setDraft(null), onDone())}>
          Cancel
        </button>
      </div>
      <details className="widget-library" open>
        <summary>Widget library</summary>
        <div className="widget-library-filters"><label>Find a widget<input autoFocus type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search tools, devices, knowledge…" /></label><label>Category<select value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map(name => <option key={name}>{name}</option>)}</select></label></div>
        <div className="widget-catalog" role="list" aria-label="Widget library">
          {matches.map(([id, panel]) => {
            const selected = !(shown.hidden ?? []).includes(id);
            return <div key={id} role="listitem" className="widget-catalog-item"><div><strong>{panel.label}</strong><span className="muted small">{widgetInfo(page, id).category}</span></div><p className="small">{widgetInfo(page, id).description}</p><div className="row-gap"><span className="muted small spacer">{required.includes(id) ? "Required" : !available(id) ? "Currently unavailable" : selected ? "Added" : "Hidden"}</span><button type="button" className="btn2" disabled={required.includes(id) || (!selected && !available(id))} aria-label={`${selected ? "Remove" : "Add"} ${panel.label} widget`} onClick={() => change(id, selected ? "hidden" : "main", null)}>{selected ? "Remove" : "Add"}</button></div></div>;
          })}
          {!matches.length && <p className="muted small" role="status">No widgets match your search.</p>}
        </div>
      </details>
      {(shown.hidden ?? []).length > 0 && (
        <div className="row-gap" role="list" aria-label="Hidden panels">
          {shown.hidden.map((id) => (
            <span key={id} role="listitem" className="chip n">
              {built[id]?.label ?? id}{" "}
              <button type="button" className="linkish small" disabled={!available(id)} onClick={() => change(id, "main", null)} aria-label={`Show ${built[id]?.label ?? id}`}>
                Show
              </button>
            </span>
          ))}
        </div>
      )}
      {error && <p className="error small" role="alert">Layout was not saved: {error.message}</p>}

    </div>
  );
  const zone = (col: Zone) => {
    const wide = col === "top" || col === "bottom";
    const ids = shown[col] ?? [];
    if (wide && !editing && ids.length === 0) return null;
    return (
      <div
        className="stack"
        style={wide ? { gridColumn: "1 / -1" } : undefined}
        role={editing ? "region" : undefined}
        aria-label={editing ? ZONES.find(([z]) => z === col)![1] : undefined}
        onDragOver={(e) => editing && e.preventDefault()}
        onDrop={(e) => {
          if (!editing) return;
          e.preventDefault();
          const from = e.dataTransfer.getData("text/plain");
          if (from) change(from, col, null);
        }}
      >
        {col === "top" && arrangeBar}
        {ids.map((id) => frame(id, col))}
        {editing && (
          <div className="arrange-drop muted small">
            {wide ? `Drop here to put a panel ${col === "top" ? "across the top" : "across the bottom"}, full width` : "Drop here to put a panel at the end"}
          </div>
        )}
      </div>
    );
  };
  return <>
    {editing && <fieldset className="widget-editor" disabled={busy} aria-label="Widget builder">{zone("top")}{zone("main")}{zone("side")}{zone("bottom")}</fieldset>}
    {!editorOnly && <div className="arrange-live" hidden={editing}>{ZONES.map(([col]) => {
      const wide = col === "top" || col === "bottom";
      const ids = layout[col] ?? [];
      if (wide && !ids.length) return null;
      return <div key={col} className="stack" style={wide ? { gridColumn: "1 / -1" } : undefined}>{ids.map(id => built[id] ? <div key={id}>{built[id].node}</div> : null)}</div>;
    })}</div>}
  </>;
}

/** A field group while arranging: move fields up and down, make them full width, hide them. */
function FieldsEditor({ title, labels, value, onChange }: { title: string; labels: Record<string, string>; value: FieldLayout | undefined; onChange: (v: FieldLayout) => void }) {
  const ids = Object.keys(labels);
  const v = { order: value?.order ?? ids, hidden: value?.hidden ?? [], wide: value?.wide ?? [] };
  const order = [...v.order.filter((f) => f in labels), ...ids.filter((f) => !v.order.includes(f))];
  const swap = (i: number, j: number) => {
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    onChange({ ...v, order: next });
  };
  const toggle = (key: "hidden" | "wide", f: string, on: boolean) => onChange({ ...v, order, [key]: on ? [...v[key], f] : v[key].filter((x) => x !== f) });
  return (
    <fieldset className="arrange-fields" aria-label={title}>
      <legend className="small">{title}</legend>
      {order.map((f, i) => (
        <div key={f} className="arrange-field small" role="group" aria-label={`Field: ${labels[f]}`}>
          <span className="spacer">{labels[f]}</span>
          <button type="button" className="linkish small" disabled={i === 0} onClick={() => swap(i, i - 1)} aria-label={`Move ${labels[f]} up`}>
            Up
          </button>
          <button type="button" className="linkish small" disabled={i === order.length - 1} onClick={() => swap(i, i + 1)} aria-label={`Move ${labels[f]} down`}>
            Down
          </button>
          <label className="inline">
            <input type="checkbox" checked={v.wide.includes(f)} onChange={(e) => toggle("wide", f, e.target.checked)} /> Full width
          </label>
          <label className="inline">
            <input type="checkbox" checked={!v.hidden.includes(f)} onChange={(e) => toggle("hidden", f, !e.target.checked)} /> Shown
          </label>
        </div>
      ))}
    </fieldset>
  );
}
