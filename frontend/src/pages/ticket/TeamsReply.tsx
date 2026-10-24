// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";

export function TeamsReply({ ticketId, onDone }: { ticketId: number; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const [chat, setChat] = useState("");
  const [delivery, setDelivery] = useState(() => crypto.randomUUID());
  const connection = useQuery({ queryKey: ["teams-connection"], queryFn: api.teams.connection });
  const chats = useQuery({ queryKey: ["ticket", ticketId, "teams-chats"], queryFn: () => api.teams.chats(ticketId), enabled: open && !!connection.data?.connected, retry: false });
  const send = useMutation({ mutationFn: () => api.teams.send(ticketId, { body, chat_id: chat, delivery_id: delivery }),
    onSuccess: () => { setBody(""); setDelivery(crypto.randomUUID()); onDone(); }, retry: false });
  if (!connection.data?.configured) return null;
  return <section className="card stack" aria-label="Teams reply">
    <button className="btn2" type="button" onClick={() => setOpen(!open)} aria-expanded={open}>Message requester in Teams</button>
    {open && (!connection.data.connected ? <p><Link to="/preferences">Connect your Microsoft account in My preferences</Link> to send as yourself.</p> : <>
      <p className="muted">Send as {connection.data.account}. This sends only the text below to the requester and saves a public reply on this ticket. Internal notes are not included.</p>
      {chats.data?.length === 0 && <p>No matching one-to-one chat found. Start a chat with the requester in Teams, then <button className="linkish" type="button" onClick={() => chats.refetch()}>refresh chats</button>.</p>}
      {!!chats.data?.length && <>
        <label>Teams recipient<select value={chat} onChange={e => setChat(e.target.value)} disabled={send.isPending || send.isError}>
          <option value="">Choose the requester’s chat…</option>
          {chats.data.map(c => <option key={c.id} value={c.id}>{c.recipient}</option>)}
        </select></label>
        <label>Teams message<textarea value={body} maxLength={8000} onChange={e => setBody(e.target.value)} disabled={send.isPending || send.isError} /></label>
        <button className="btn" type="button" disabled={!chat || !body.trim() || send.isPending || send.isError} onClick={() => send.mutate()}>Send Teams message</button>
      </>}
      {send.isSuccess && <p role="status">Sent in Teams and saved on this ticket.</p>}
      {send.error && <><p className="error" role="alert">{send.error.message}</p><p>Check the chat in Teams before trying again.</p>
        <button className="btn2" type="button" onClick={() => { send.reset(); setDelivery(crypto.randomUUID()); }}>I checked Teams — prepare a new send</button></>}
      {chats.error && <p className="error" role="alert">{chats.error.message}</p>}
    </>)}
  </section>;
}
