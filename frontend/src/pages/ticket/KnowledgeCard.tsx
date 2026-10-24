// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, type KbArticleSummary, type Ticket } from "../../api/client";

/** Articles that match the ticket, solved tickets like it, the articles used on it, and a draft article once it's solved. */
export function KnowledgeCard({ ticket }: { ticket: Ticket }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const key = ["ticket", ticket.id, "kb"];
  const kb = useQuery({ queryKey: key, queryFn: () => api.kb.forTicket(ticket.id) });
  const done = (data: unknown) => {
    queryClient.setQueryData(key, data);
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id] });
  };
  const link = useMutation({ mutationFn: (articleId: number) => api.kb.link(ticket.id, articleId), onSuccess: done });
  const unlink = useMutation({ mutationFn: (articleId: number) => api.kb.unlink(ticket.id, articleId), onSuccess: done });
  const draft = useMutation({ mutationFn: () => api.kb.draft(ticket.id), onSuccess: (a) => navigate(`/kb/${a.id}/edit`) });
  const [copied, setCopied] = useState<number | null>(null);
  const solved = ["resolved", "closed"].includes(ticket.status);
  const error = link.error ?? unlink.error ?? draft.error;

  const copy = async (a: KbArticleSummary) => {
    try {
      await navigator.clipboard.writeText(`${a.title}: ${window.location.origin}/help/${a.id}`);
      setCopied(a.id);
    } catch {
      setCopied(null);
    }
  };
  const title = (a: KbArticleSummary) => (
    <Link to={`/kb/${a.id}`} target="_blank">
      {a.title}
    </Link>
  );

  return (
    <div className="card stack" style={{ gap: 8 }} role="region" aria-label="Knowledge base">
      <div className="lbl" style={{ margin: 0 }}>
        Knowledge base
      </div>
      {kb.data && kb.data.linked.length > 0 && (
        <div className="stack small" style={{ gap: 4 }} aria-label="Used on this ticket">
          <span className="muted">Used on this ticket</span>
          {kb.data.linked.map((a) => (
            <span key={a.id} className="row-gap" style={{ gap: 6 }}>
              {title(a)}
              <button className="linkish small" type="button" aria-label={`Not used: ${a.title}`} onClick={() => unlink.mutate(a.id)}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {kb.data?.articles.length === 0 && kb.data.similar.length === 0 && kb.data.linked.length === 0 && <span className="muted small">No matching articles or solved tickets.</span>}
      {kb.data && kb.data.articles.length > 0 && (
        <div className="stack small" style={{ gap: 6 }} aria-label="Matching articles">
          <span className="muted">Articles that may help</span>
          {kb.data.articles.map((a) => (
            <div key={a.id} className="stack" style={{ gap: 2 }}>
              {title(a)}
              <span className="row-gap" style={{ gap: 8 }}>
                <button className="linkish small" type="button" onClick={() => link.mutate(a.id)}>
                  Used it
                </button>
                {a.audience === "everyone" && (
                  <button className="linkish small" type="button" onClick={() => copy(a)}>
                    {copied === a.id ? "Link copied" : "Copy link for the requester"}
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      {kb.data && kb.data.similar.length > 0 && (
        <div className="stack small" style={{ gap: 4 }} aria-label="Similar solved tickets">
          <span className="muted">Similar solved tickets</span>
          {kb.data.similar.map((s) => (
            <Link key={s.id} to={`/tickets/${s.id}`}>
              {s.label} {s.subject}
            </Link>
          ))}
        </div>
      )}
      {error && <span className="error small">{error.message}</span>}
      <div className="row-gap small">
        <Link to="/kb">Search the knowledge base</Link>
        {solved && (
          <button className="linkish small" type="button" disabled={draft.isPending} onClick={() => draft.mutate()}>
            Draft an article from this ticket
          </button>
        )}
      </div>
    </div>
  );
}
