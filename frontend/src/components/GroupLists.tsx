import { useQuery } from "@tanstack/react-query";

import { api, type ADGroup } from "../api/client";

/** Settings > Directory's group lists (e.g. every "[EMAIL]" distribution list) as tick boxes. */
export function GroupLists({ isTicked, onToggle }: { isTicked: (dn: string) => boolean; onToggle: (g: ADGroup, ticked: boolean) => void }) {
  const lists = useQuery({ queryKey: ["ad-group-lists"], queryFn: api.ad.groupLists, staleTime: 60_000 });
  if (lists.error) return <span className="error small">{lists.error.message}</span>;
  return (
    <>
      {lists.data
        ?.filter((l) => l.groups.length > 0)
        .map((l) => (
          <fieldset key={l.label} className="filter-box">
            <legend>{l.label}</legend>
            <div className="tick-grid">
              {l.groups.map((g) => (
                <label key={g.dn} className="inline small" title={g.dn}>
                  <input type="checkbox" checked={isTicked(g.dn)} onChange={(e) => onToggle(g, e.target.checked)} /> {g.name.startsWith(l.prefix) ? g.name.slice(l.prefix.length).trim() || g.name : g.name}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
    </>
  );
}
