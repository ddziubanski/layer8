import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type ADGroup } from "../api/client";

/** Search-as-you-type for AD security groups technicians may change. */
export function GroupPicker({ label, exclude = [], onPick }: { label: string; exclude?: string[]; onPick: (g: ADGroup) => void }) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  const found = useQuery({ queryKey: ["ad-groups", q], queryFn: () => api.ad.groups(q), enabled: q.length >= 2 });
  const hits = (found.data ?? []).filter((g) => !exclude.some((e) => e.toLowerCase() === g.dn.toLowerCase()));
  return (
    <label className="field picker">
      {label}
      <input type="search" placeholder="Type part of a group's name" value={text} onChange={(e) => setText(e.target.value)} />
      {q.length >= 2 && hits.length > 0 && (
        <ul className="picker-list" role="listbox">
          {hits.map((g) => (
            <li key={g.dn} role="option" aria-selected={false} onMouseDown={(e) => (e.preventDefault(), onPick(g), setText(""))}>
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
