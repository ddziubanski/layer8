// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { api, type Device, type NetworkTarget } from "../api/client";
import { DeviceSource, deviceSource, networkTargetKey } from "./DeviceSource";

/** Search devices by name, host name, OS or person, and pick one (a combobox). Clears after each pick. */
export function DevicePicker({ label, onPick, exclude = [], ticketId, onPickNetwork, excludeNetwork = [], disabled = false }: {
  label: string; onPick: (device: Device) => void; exclude?: number[]; ticketId?: number;
  onPickNetwork?: (target: NetworkTarget) => void; excludeNetwork?: string[]; disabled?: boolean;
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
  const results = useQuery({ queryKey: ["device-search", debounced], queryFn: () => api.devices.list({ q: debounced, limit: 10 }), enabled: debounced.length >= 2 });
  const network = useQuery({ queryKey: ["ticket-device-candidates", ticketId, debounced], queryFn: () => api.ticketNetwork.candidates(ticketId!, debounced), enabled: !!ticketId && !!onPickNetwork && debounced.length >= 2 });
  type Hit = { id: string; name: string; detail: string; source: string; device?: Device; target?: NetworkTarget };
  const hits: Hit[] = debounced.length >= 2 && text.trim() === debounced ? [
    ...(results.data?.items ?? []).filter(d => !exclude.includes(d.id)).map(d => ({ id: `asset-${d.id}`, name: d.name, detail: [d.os, d.assigned_user?.display_name].filter(Boolean).join(" · "), source: deviceSource(d), device: d })),
    ...(onPickNetwork ? network.data ?? [] : []).filter(t => !excludeNetwork.includes(networkTargetKey(t))).map(t => ({ id: networkTargetKey(t), name: t.name, detail: [t.address, t.mac, t.source_name, t.stale && "Stale observation"].filter(Boolean).join(" · "), source: t.source_label, target: t })),
  ] : [];
  const selected = Math.min(active, Math.max(0, hits.length - 1));

  function pick(hit: Hit) {
    if (disabled) return;
    if (hit.device) onPick(hit.device);
    if (hit.target) onPickNetwork?.(hit.target);
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
        aria-activedescendant={open && hits.length ? `${id}-option-${selected}` : undefined}
        placeholder={onPickNetwork ? "Name, IP, MAC, site or person" : "Type a device name, OS or person"}
        disabled={disabled}
        maxLength={200}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
          if (!hits.length || (onPickNetwork && network.isFetching) || results.isFetching) return;
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
          {hits.map((d, i) => (
            <li
              key={d.id}
              id={`${id}-option-${i}`}
              role="option"
              aria-selected={i === selected}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(d);
              }}
            >
              {d.name} {onPickNetwork && <DeviceSource label={d.source} />} <span className="muted small">{d.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {open && (results.isFetching || (onPickNetwork && network.isFetching)) && <span className="muted small" role="status">Searching devices…</span>}
      {open && debounced.length >= 2 && results.isSuccess && (!onPickNetwork || network.isSuccess) && hits.length === 0 && <span className="muted small">No device matches.</span>}
      {open && (results.error || (onPickNetwork && network.error)) && <span className="error small" role="alert">{results.error?.message ?? network.error?.message}</span>}
    </label>
  );
}
