import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { api, type User } from "../api/client";

type Picked = Pick<User, "id" | "display_name" | "email">;

/** Search people by name or email and pick one (a combobox). */
export function UserPicker({
  label,
  value,
  onChange,
  placeholder = "Type a name or email",
  autoFocus = false,
}: {
  label: string;
  value: Picked | null;
  onChange: (user: Picked | null) => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);
  const results = useQuery({ queryKey: ["user-search", debounced], queryFn: () => api.searchUsers(debounced), enabled: debounced.length >= 2 });
  const hits = debounced.length >= 2 && text.trim() === debounced ? (results.data ?? []) : [];
  const selected = Math.min(active, Math.max(0, hits.length - 1));

  function pick(u: Picked) {
    onChange(u);
    setText("");
    setOpen(false);
  }

  if (value) {
    return (
      <div className="field">
        {label}
        <div className="picked">
          <span>
            {value.display_name} <span className="muted small">{value.email}</span>
          </span>
          <button type="button" className="linkish small" onClick={() => onChange(null)} aria-label={`Clear ${label.toLowerCase()}`}>
            Change
          </button>
        </div>
      </div>
    );
  }
  return (
    <label className="field picker">
      {label}
      <input
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={open && hits.length ? `${id}-option-${selected}` : undefined}
        autoFocus={autoFocus}
        placeholder={placeholder}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
          if (e.nativeEvent.isComposing) return;
          if (!hits.length) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive(open ? Math.min(selected + 1, hits.length - 1) : 0);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
            setActive(open ? Math.max(selected - 1, 0) : hits.length - 1);
          } else if (e.key === "Enter" && open) {
            e.preventDefault();
            pick(hits[selected]);
          }
        }}
      />
      {open && hits.length > 0 && (
        <ul className="picker-list" role="listbox" id={`${id}-list`}>
          {hits.map((u, i) => (
            <li
              key={u.id}
              id={`${id}-option-${i}`}
              role="option"
              aria-selected={i === selected}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(u);
              }}
            >
              {u.display_name} <span className="muted small">{u.email}</span>
              {u.department && <span className="muted small"> · {u.department}</span>}
            </li>
          ))}
        </ul>
      )}
      {open && debounced.length >= 2 && results.isSuccess && hits.length === 0 && <span className="muted small">No one matches.</span>}
    </label>
  );
}
