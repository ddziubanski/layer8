// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Notice } from "../api/client";
import { can } from "../permissions";
import { ME_KEY } from "../queryClient";

export const SEVERITY: Record<string, { label: string; chip: string; icon: string }> = {
  outage: { label: "Outage", chip: "r", icon: "⛔" },
  warning: { label: "Warning", chip: "a", icon: "⚠" },
  info: { label: "Information", chip: "n", icon: "ℹ" },
};

// Per tab: which notices were already shown after sign-in, and which banners were closed.
const SEEN = "helpdesk.notices.seen";
const HIDDEN = "helpdesk.notices.hidden";

function readIds(key: string): number[] {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "[]");
  } catch {
    return [];
  }
}

function writeIds(key: string, ids: number[]) {
  try {
    sessionStorage.setItem(key, JSON.stringify(ids));
  } catch {
    // Not remembered; harmless.
  }
}

function useCurrentNotices() {
  return useQuery({ queryKey: ["notices", "current"], queryFn: api.notices.current, refetchInterval: 60_000 }).data ?? [];
}

/** For technicians allowed to manage notices: turn this one off for everyone, right here. */
function TurnOff({ n, onOff }: { n: Notice; onOff?: () => void }) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, staleTime: Infinity }).data;
  const off = useMutation({
    mutationFn: () => api.notices.setEnabled(n.id, false),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notices"] });
      onOff?.();
    },
  });
  if (!me || me.portal_only || !can(me, "notices_manage")) return null;
  return (
    <button type="button" className="linkish small" disabled={off.isPending} aria-label={`Turn off notice for everyone: ${n.title}`}
      title="Stops showing it to everyone; turn it back on in Settings > Notices" onClick={() => off.mutate()}>
      {off.isError ? "Couldn't turn off; retry" : "Turn off for everyone"}
    </button>
  );
}

function NoticeText({ n }: { n: Notice }) {
  const s = SEVERITY[n.severity] ?? SEVERITY.info;
  return (
    <>
      <span className={`chip ${s.chip}`}>
        <span aria-hidden="true">{s.icon}</span> {s.label}
      </span>{" "}
      <strong>{n.title}</strong>
      {n.message && <span className="notice-message"> {n.message}</span>}
    </>
  );
}

/** Current notices across the top of the console or portal. Each can be hidden for this tab. */
export function NoticeBanner() {
  const notices = useCurrentNotices();
  const [hidden, setHidden] = useState<number[]>(() => readIds(HIDDEN));
  const shown = notices.filter((n) => !hidden.includes(n.id));
  if (!shown.length) return null;
  return (
    <div className="notices" role="region" aria-label="Service notices">
      {shown.map((n) => (
        <div key={n.id} className={`notice notice-${n.severity}`}>
          <span className="spacer">
            <NoticeText n={n} />
          </span>
          <TurnOff n={n} />
          <button
            type="button"
            className="linkish small"
            aria-label={`Hide notice: ${n.title}`}
            onClick={() => {
              const next = [...hidden, n.id];
              setHidden(next);
              writeIds(HIDDEN, next);
            }}
          >
            Hide
          </button>
        </div>
      ))}
    </div>
  );
}

/** Right after sign-in: the notices this person hasn't seen yet in this tab. */
export function NoticeDialog() {
  const notices = useCurrentNotices();
  const [seen, setSeen] = useState<number[]>(() => readIds(SEEN));
  const fresh = notices.filter((n) => !seen.includes(n.id));
  const close = () => {
    const next = [...seen, ...fresh.map((n) => n.id)];
    setSeen(next);
    writeIds(SEEN, next);
  };
  useEffect(() => {
    if (!fresh.length) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  if (!fresh.length) return null;
  return (
    <div className="overlay" onClick={close}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="notices-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="notices-title">{fresh.some((n) => n.severity === "outage") ? "Current outage" : "Service notice"}</h2>
        {fresh.map((n) => (
          <p key={n.id} style={{ margin: 0 }}>
            <NoticeText n={n} /> <TurnOff n={n} />
          </p>
        ))}
        <div className="row-gap">
          <span className="spacer" />
          <button className="btn" type="button" onClick={close} autoFocus>
            OK
          </button>
        </div>
      </div>
    </div>
  );
}
