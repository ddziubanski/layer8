// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Me } from "../api/client";
import { can, uses } from "../permissions";

export function Action1RemoteControl({ me, ticketId, deviceId, onChanged }: {
  me?: Me; ticketId: number; deviceId: number; onChanged?: () => void;
}) {
  if (!me || !uses(me, "action1_remote") || !can(me, "action1_remote")) return null;
  return <RemoteButton key={`${me.id}:${ticketId}:${deviceId}`} ticketId={ticketId} deviceId={deviceId} onChanged={onChanged} />;
}

function RemoteButton({ ticketId, deviceId, onChanged }: { ticketId: number; deviceId: number; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const running = useRef(false);
  const alive = useRef(true);
  const queryClient = useQueryClient();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function launch() {
    if (running.current) return;
    // Open during the click gesture. Never request a session if popups are blocked.
    const viewer = window.open("about:blank", "_blank");
    if (!viewer) { setFailed(true); setMessage("Allow a new tab for Layer 8, then try again. No session was requested."); return; }
    viewer.opener = null;
    viewer.document.title = "Connecting to Action1";
    const referrer = viewer.document.createElement("meta");
    referrer.name = "referrer"; referrer.content = "no-referrer";
    viewer.document.head.appendChild(referrer);
    viewer.document.body.textContent = "Layer 8 is requesting Action1 remote assistance. Waiting for the endpoint…";
    running.current = true;
    setBusy(true); setFailed(false); setMessage("Waiting for Action1 and endpoint consent…");
    let opened = false;
    try {
      const start = await api.remote.action1Start(ticketId, deviceId);
      if (["failed", "unknown", "expired"].includes(start.status)) throw new Error(start.message);
      for (let attempt = 0; attempt < 13; attempt++) {
        if (!alive.current || viewer.closed) throw new Error("Remote launch cancelled. Check Action1 before requesting another session.");
        const result = await api.remote.action1Poll(ticketId, start.session_id);
        if (result.status === "ready" && result.launch_url) {
          // No React Query cache, local storage or ticket note ever receives this URL.
          if (!alive.current || viewer.closed) throw new Error("Remote launch cancelled. Check Action1 before requesting another session.");
          viewer.location.replace(result.launch_url);
          opened = true;
          if (alive.current) setMessage("Action1 opened in a new tab. End the connection in Action1; mark tracking ended in Device tools afterward.");
          return;
        }
        if (["failed", "unknown", "expired"].includes(result.status)) throw new Error(result.message);
        if (attempt < 12) await new Promise(resolve => setTimeout(resolve, 5000));
      }
      throw new Error("Action1 is still preparing the connection. Check Action1 before trying again; Layer 8 will reuse the open tracking entry.");
    } catch (error) {
      if (alive.current) { setFailed(true); setMessage(error instanceof Error ? error.message : "Action1 remote launch failed"); }
    } finally {
      if (!opened && !viewer.closed) viewer.close();
      running.current = false;
      queryClient.invalidateQueries({ queryKey: ["remote-sessions", ticketId] });
      onChanged?.();
      if (alive.current) setBusy(false);
    }
  }
  return <div className="stack" style={{ gap: 6 }}>
    <button className="btn2" type="button" disabled={busy} onClick={() => void launch()}>{busy ? "Connecting to Action1…" : "Action1 remote desktop"}</button>
    {message && <p className={`small ${failed ? "error" : "muted"}`} role={failed ? "alert" : "status"} style={{ margin: 0 }}>{message}</p>}
  </div>;
}
