import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { api, type Device } from "../api/client";

/** Search devices by name, host name, OS or person, and pick one (a combobox). Clears after each pick. */
export function DevicePicker({ label, onPick, exclude = [] }: { label: string; onPick: (device: Device) => void; exclude?: number[] }) {
  const id = useId();
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);
  const results = useQuery({ queryKey: ["device-search", debounced], queryFn: () => api.devices.list({ q: debounced, limit: 10 }), enabled: debounced.length >= 2 });
  const hits = debounced.length >= 2 ? (results.data?.items ?? []).filter((d) => !exclude.includes(d.id)) : [];

  function pick(d: Device) {
    onPick(d);
    setText("");
    setOpen(false);
  }

  return (
    <label className="field picker">
      {label}
      <input
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        placeholder="Type a device name, OS or person"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!hits.length) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, hits.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            pick(hits[active]);
          }
        }}
      />
      {open && hits.length > 0 && (
        <ul className="picker-list" role="listbox" id={`${id}-list`}>
          {hits.map((d, i) => (
            <li
              key={d.id}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(d);
              }}
            >
              {d.name} <span className="muted small">{[d.os, d.assigned_user?.display_name].filter(Boolean).join(" · ")}</span>
            </li>
          ))}
        </ul>
      )}
      {open && debounced.length >= 2 && results.isSuccess && hits.length === 0 && <span className="muted small">No device matches.</span>}
    </label>
  );
}
