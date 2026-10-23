import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { Markdown } from "../../components/Markdown";

type Turn = { question: string; answer: string; citations: { title: string; url: string }[] };

/** Ask Microsoft 365 Copilot about this ticket, as yourself (Settings > Teams > Copilot on tickets). */
export function CopilotCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const connection = useQuery({ queryKey: ["teams-connection"], queryFn: api.teams.connection });
  const [question, setQuestion] = useState("");
  const [share, setShare] = useState(true);
  const [conversation, setConversation] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const ask = useMutation({
    mutationFn: () => api.teams.copilot(ticket.id, { question, include_ticket: share, conversation_id: conversation }),
    onSuccess: (r) => {
      setConversation(r.conversation_id);
      setTurns((t) => [...t, { question, answer: r.answer, citations: r.citations }]);
      setQuestion("");
      onChanged();
    },
  });
  const note = useMutation({
    mutationFn: (t: Turn) => api.addComment(ticket.id, `Copilot, asked "${t.question}":\n\n${t.answer}`, true, false),
    onSuccess: onChanged,
  });

  if (!connection.data) return null;
  if (!connection.data.connected || !connection.data.copilot) {
    return (
      <p className="small" style={{ margin: 0 }}>
        <Link to="/preferences">{connection.data.connected ? "Reconnect" : "Connect"} your Microsoft account in My preferences</Link> to ask Copilot. You need a Microsoft
        365 Copilot licence.
      </p>
    );
  }
  return (
    <div className="stack copilot-card" style={{ gap: 10 }}>
      {turns.map((t, i) => (
        <div key={i} className="stack" style={{ gap: 6 }}>
          <div className="small muted">You asked: {t.question}</div>
          <div className="md copilot-answer">
            <Markdown text={t.answer} />
          </div>
          {t.citations.length > 0 && (
            <ul className="small copilot-sources" aria-label="Sources">
              {t.citations.map((c) => (
                <li key={c.url}>
                  <a href={c.url} target="_blank" rel="noreferrer">
                    {c.title}
                  </a>
                </li>
              ))}
            </ul>
          )}
          <div>
            <button className="linkish small" type="button" disabled={note.isPending} onClick={() => note.mutate(t)}>
              Add as an internal note
            </button>
          </div>
        </div>
      ))}
      <form
        className="stack"
        style={{ gap: 6 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim()) ask.mutate();
        }}
      >
        <label className="field">
          {turns.length ? "Ask a follow-up" : "Ask Copilot"}
          <textarea rows={3} maxLength={4000} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. What usually fixes this? Draft a reply to the requester." />
        </label>
        <label className="inline small">
          <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> Include this ticket&apos;s text (subject, description, latest replies and notes)
        </label>
        <div className="row-gap">
          <button className="btn2" type="submit" disabled={!question.trim() || ask.isPending}>
            {ask.isPending ? "Asking…" : "Ask"}
          </button>
          {conversation && (
            <button className="linkish small" type="button" onClick={() => (setConversation(null), setTurns([]))}>
              Start over
            </button>
          )}
        </div>
      </form>
      {ask.error && <p className="error">{ask.error.message}</p>}
      {note.isSuccess && <p className="small" role="status">Added as an internal note.</p>}
      <p className="muted small" style={{ margin: 0 }}>
        Asked as {connection.data.account}. Copilot answers from what you can see in Microsoft 365; check its answer before using it.
      </p>
    </div>
  );
}
