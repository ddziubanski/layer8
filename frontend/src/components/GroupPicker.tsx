import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { api, type ADGroup } from "../api/client";

/** Search-as-you-type for AD security groups technicians may change. */
export function GroupPicker({ label, exclude = [], onPick }: { label: string; exclude?: string[]; onPick: (g: ADGroup) => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  const found = useQuery({ queryKey: ["ad-groups", q], queryFn: () => api.ad.groups(q), enabled: q.length >= 2 });
  const hits = text.trim() === q ? (found.data ?? []).filter((g) => !exclude.some((e) => e.toLowerCase() === g.dn.toLowerCase())) : [];
  const selected = Math.min(active, Math.max(0, hits.length - 1));
  const pick = (g: ADGroup) => { onPick(g); setText(""); setOpen(false); };
  return (
    <label className="field picker">
      {label}
      <input type="search" role="combobox" aria-autocomplete="list" aria-expanded={open && hits.length > 0} aria-controls={`${id}-list`} aria-activedescendant={open && hits.length ? `${id}-option-${selected}` : undefined}
        placeholder="Type part of a group's name" value={text} onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); }}
        onBlur={() => setOpen(false)} onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
          else if (hits.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault(); setOpen(true);
            setActive(e.key === "ArrowDown" ? (open ? Math.min(selected + 1, hits.length - 1) : 0) : (open ? Math.max(selected - 1, 0) : hits.length - 1));
          } else if (e.key === "Enter" && open && hits.length) { e.preventDefault(); pick(hits[selected]); }
        }} />
      {open && q.length >= 2 && hits.length > 0 && (
        <ul className="picker-list" role="listbox" id={`${id}-list`}>
          {hits.map((g, i) => (
            <li key={g.dn} id={`${id}-option-${i}`} role="option" aria-selected={i === selected} onMouseDown={(e) => (e.preventDefault(), pick(g))}>
              {g.name} <span className="muted small">{g.dn.split(",").slice(1, 3).join(",")}</span>
            </li>
          ))}
        </ul>
      )}
      {found.error && <span className="error small">{found.error.message}</span>}
      {q.length >= 2 && found.isSuccess && hits.length === 0 && <span className="muted small">No group you may change matches.</span>}
    </label>
  );
}
