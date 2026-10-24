// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Ticket } from "../../api/client";
import { Markdown } from "../../components/Markdown";

type Turn = {
  id: string;
  question: string;
  answer: string;
  citations: { title: string; url: string }[];
  include: boolean;
  send: boolean;
  draft: string;
  saved?: string;
};

export function CopilotSession({ ticket, account, onChanged }: { ticket: Ticket; account: string; onChanged: () => void }) {
  const [question, setQuestion] = useState("");
  const [share, setShare] = useState(true);
  const [internal, setInternal] = useState(false);
  const [review, setReview] = useState(false);
  const [conversation, setConversation] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sharingChanged, setSharingChanged] = useState(false);
  const preview = useQuery({
    queryKey: ["ticket", ticket.id, "copilot-context", internal],
    queryFn: () => api.teams.copilotContext(ticket.id, internal),
    enabled: share && review,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const ask = useMutation({
    mutationFn: (body: Parameters<typeof api.teams.copilot>[1]) => api.teams.copilot(ticket.id, body),
    retry: false,
    onSuccess: (result, submitted) => {
      setConversation(result.conversation_id);
      setTurns(t => [...t, { id: crypto.randomUUID(), question: submitted.question, answer: result.answer,
        citations: result.citations, include: false, send: false, draft: result.answer }]);
      setQuestion("");
      setSharingChanged(false);
      onChanged();
    },
  });
  const publish = useMutation({
    mutationFn: (turn: Turn) => {
      const sources = turn.citations.map(c => `- ${c.title}: ${c.url}`).join("\n");
      const text = turn.send ? turn.draft.trim()
        : `Copilot, asked "${turn.question}":\n\n${turn.draft.trim()}${sources ? `\n\nSources:\n${sources}` : ""}`;
      return api.addComment(ticket.id, text, !turn.send, turn.send);
    },
    retry: false,
    onSuccess: (comment, turn) => {
      const saved = turn.send
        ? comment.emailed ? "Saved as a public reply; email queued for the requester." : "Saved to the requester portal. Email was not queued; check email setup."
        : "Added as an internal note.";
      setTurns(items => items.map(t => t.id === turn.id ? { ...t, saved } : t));
      onChanged();
    },
  });
  const busy = ask.isPending || publish.isPending;
  const changeTurn = (id: string, update: Partial<Turn>) => setTurns(items => items.map(t => t.id === id ? { ...t, ...update } : t));
  const changeSharing = (shareTicket: boolean, shareInternal: boolean) => {
    setShare(shareTicket);
    setInternal(shareInternal);
    setReview(false);
    setConversation(null);
    setSharingChanged(turns.length > 0);
    ask.reset();
  };

  return <div className="stack copilot-card" style={{ gap: 10 }}>
    {turns.map((turn, i) => <section key={turn.id} className="stack" style={{ gap: 6 }} aria-label={`Copilot answer ${i + 1}`}>
      <div className="small muted">You asked: {turn.question}</div>
      <div className="md copilot-answer"><Markdown text={turn.answer} /></div>
      {turn.citations.length > 0 && <ul className="small copilot-sources" aria-label="Sources">
        {turn.citations.map(c => <li key={c.url}><a href={c.url} target="_blank" rel="noreferrer">{c.title}</a></li>)}
      </ul>}
      {turn.saved ? <p className="small" role="status">{turn.saved}</p> : <>
        <label className="inline small"><input type="checkbox" checked={turn.include} disabled={busy}
          onChange={e => changeTurn(turn.id, { include: e.target.checked, send: false })} /> Include this response on the ticket</label>
        <label className="inline small"><input type="checkbox" checked={turn.send} disabled={!turn.include || busy}
          onChange={e => changeTurn(turn.id, { send: e.target.checked })} /> Send to requester</label>
        {turn.include && <>
          <label className="field">{turn.send ? "Reply to requester" : "Internal note response"}
            <textarea rows={5} value={turn.draft} disabled={busy} onChange={e => changeTurn(turn.id, { draft: e.target.value })} />
          </label>
          {turn.send && <p className="small">Review this text before sending. It becomes a public ticket reply and is emailed when email delivery is configured. Only the edited reply above is sent.</p>}
          <div><button className="btn2" type="button" disabled={busy || !turn.draft.trim()} onClick={() => publish.mutate(turn)}>
            {publish.isPending && publish.variables?.id === turn.id ? "Saving…" : turn.send ? "Send reply" : "Add as an internal note"}
          </button></div>
        </>}
      </>}
      {publish.error && publish.variables?.id === turn.id && <p role="alert" className="error">{publish.error.message} Check the ticket before retrying.</p>}
    </section>)}
    <form className="stack" style={{ gap: 6 }} onSubmit={e => {
      e.preventDefault();
      if (!busy && question.trim() && (!share || (review && preview.data && !preview.isFetching && !preview.error))) {
        ask.mutate({ question: question.trim(), include_ticket: share, include_internal: internal,
          context_digest: share ? preview.data!.digest : null, conversation_id: conversation });
      }
    }}>
      <label className="field">{conversation ? "Ask a follow-up" : "Ask Copilot"}
        <textarea rows={3} maxLength={4000} value={question} disabled={busy} onChange={e => setQuestion(e.target.value)}
          placeholder="e.g. Summarize this ticket or suggest troubleshooting steps." />
      </label>
      <label className="inline small"><input type="checkbox" checked={share} disabled={busy}
        onChange={e => changeSharing(e.target.checked, false)} /> Include this ticket&apos;s text</label>
      {share && <>
        <label className="inline small"><input type="checkbox" checked={internal} disabled={busy}
          onChange={e => changeSharing(true, e.target.checked)} /> Also include internal notes</label>
        <div><button className="btn2" type="button" disabled={busy || preview.isFetching} onClick={() => {
          setReview(true);
          if (review) preview.refetch().then(result => { if (result.isSuccess) ask.reset(); });
        }}>{review ? "Refresh context preview" : "Review ticket context"}</button></div>
        {review && preview.isFetching && <p role="status">Loading ticket context…</p>}
        {review && preview.error && <p className="error" role="alert">{preview.error.message}</p>}
        {review && preview.data && <section className="stack" aria-label="Ticket context preview">
          <p className="small">This is the ticket text that will be sent: {preview.data.characters.toLocaleString()} of at most {preview.data.limit.toLocaleString()} characters. Attachments are excluded.</p>
          <pre className="body-text small" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>{preview.data.sections.join("\n\n")}</pre>
        </section>}
      </>}
      {sharingChanged && <p role="status" className="small">Your next question starts a new conversation with these sharing choices. Earlier context remains in its previous Microsoft conversation.</p>}
      <div className="row-gap">
        <button className="btn2" type="submit" disabled={busy || !question.trim() || (share && (!review || !preview.data || preview.isFetching || !!preview.error))}>
          {ask.isPending ? "Asking…" : "Ask"}
        </button>
        {turns.length > 0 && <button className="linkish small" type="button" disabled={busy} onClick={() => {
          setConversation(null); setTurns([]); setSharingChanged(false); ask.reset(); publish.reset();
        }}>Start over</button>}
      </div>
    </form>
    {ask.error && <p className="error" role="alert">{ask.error.message}</p>}
    <p className="muted small" style={{ margin: 0 }}>Asked as {account}. Check Copilot&apos;s answer before saving or sending it. Start over clears this view; it does not delete Microsoft 365 conversations.</p>
  </div>;
}
