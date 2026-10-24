// SPDX-License-Identifier: AGPL-3.0-or-later
import { TeamsReply } from "./ticket/TeamsReply";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

/** The sticky header's height, as --ticket-head-height on the page, so the tools drawer sits just below it. */
function measureHead(head: HTMLDivElement | null) {
  const page = head?.parentElement;
  if (!head || !page || typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => page.style.setProperty("--ticket-head-height", `${Math.ceil(head.getBoundingClientRect().height)}px`));
  observer.observe(head);
  return () => observer.disconnect();
}
import { Link, useParams } from "react-router";

import { api, type Me } from "../api/client";
import { AttachedLine, AttachmentsSection } from "../components/Attachments";
import { PriorityChip, SourceChip, StatusChip, SlaChip } from "../components/Chips";
import { SOURCE_LABEL, when } from "../format";
import { TicketToolsWorkspace, DEFAULT_TICKET_WORKSPACE, type TicketTool } from "./ticket/TicketToolsWorkspace";
import { TicketNetworkContext } from "./ticket/TicketNetworkContext";
import { Composer, useComposerDraft } from "./ticket/Composer";
import { describe, type Names } from "./ticket/history";
import { ChangesCard } from "./ticket/ChangesCard";
import { ChecklistsCard } from "./ticket/ChecklistsCard";
import { ChildTickets } from "./ticket/ChildTickets";
import { ComputerCard } from "./ticket/ComputerCard";
import { DevicesCard } from "./ticket/DevicesCard";
import { ExpensesCard } from "./ticket/ExpensesCard";
import { ADAccountCard } from "./ticket/ADAccountCard";
import { ArrangedColumns, useArrangement } from "../components/Arrange";
import { SystemsCard } from "./ticket/SystemsCard";
import { KnowledgeCard } from "./ticket/KnowledgeCard";
import { CopilotCard } from "./ticket/CopilotCard";
import { GuestWifiCard } from "./ticket/GuestWifiCard";
import { TimeCard } from "./ticket/TimeCard";
import { TicketVmsCard } from "../components/Nutanix";
import { can, uses } from "../permissions";
import { ME_KEY } from "../queryClient";
import { ScriptsCard } from "./ticket/ScriptsCard";
import { ActionsCard, AssignmentCard, RequesterCard, StatusCard } from "./ticket/SidePanels";

// Until the saved layout loads (the same as app.layouts' usual one).
const USUAL_LAYOUT = {
  top: [],
  main: ["conversation", "attachments", "children", "checklists", "expenses", "ad_account", "scripts", "history"],
  side: ["assignment", "requester", "computer", "devices", "systems", "changes", "knowledge", "status", "actions"],
  bottom: [],
  hidden: [],
  fields: {},
};

export function TicketPage({ me }: { me: Me }) {
  const id = Number(useParams().ticketId);
  return <TicketView key={`${me.id}:${id}`} me={me} id={id} />;
}

function TicketView({ me, id }: { me: Me; id: number }) {
  const composerDraft = useComposerDraft();
  // Apply layout-mode changes on the next ticket navigation, never under an active draft.
  const [drawerMode] = useState((me.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE).mode !== "classic");
  const [toolRequest, setToolRequest] = useState<{ tool: TicketTool; sequence: number } | null>(null);
  const openTool = (tool: TicketTool) => setToolRequest(current => ({ tool, sequence: (current?.sequence ?? 0) + 1 }));
  const queryClient = useQueryClient();
  // The signed-in user with their features (the page's `me` is the plain user record).
  const signedIn = useQuery({ queryKey: ME_KEY, queryFn: api.me });
  const arrangement = useArrangement("ticket");
  const key = ["ticket", id];

  const ticket = useQuery({ queryKey: key, queryFn: () => api.ticket(id), refetchInterval: 30_000 });
  const comments = useQuery({ queryKey: [...key, "comments"], queryFn: () => api.comments(id) });
  const files = useQuery({ queryKey: [...key, "attachments"], queryFn: () => api.attachments(id) });
  const events = useQuery({ queryKey: [...key, "events"], queryFn: () => api.events(id) });
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });

  function refresh() {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["tickets"] });
    queryClient.invalidateQueries({ queryKey: ["views"] });
  }

  // Files dropped on the Attachments section go on an internal note, like any work note.
  const dropped = useMutation({
    mutationFn: async (list: File[]) => {
      const note = await api.addComment(id, `Added files: ${list.map((f) => f.name).join(", ")}`, true, false);
      for (const f of list) await api.upload(id, f, note.id);
    },
    onSettled: refresh,
  });

  if (ticket.error) {
    return (
      <p className="error">
        {ticket.error.message}. <Link to="/">Back to the queue</Link>
      </p>
    );
  }
  if (!ticket.data) return null;
  const t = ticket.data;

  const names: Names = {
    queue: (qid) => queues.data?.find((q) => q.id === qid)?.name ?? `queue ${qid}`,
    user: (uid) => (uid === me.id ? "you" : (users.data?.find((u) => u.id === uid)?.display_name ?? `user ${uid}`)),
  };
  const filesFor = (commentId: number | null) => (files.data ?? []).filter((f) => f.comment_id === commentId);

  const activeMe = signedIn.data ?? me;
  const effectiveLayout = arrangement.layout?.effective ?? USUAL_LAYOUT;
  const hidden = new Set(effectiveLayout.hidden ?? []);
  const panels = {
          conversation: {
            label: "Conversation",
            node: (
              <div className="stack ticket-conversation">
                {t.merged_into && (
                  <div className="card note" role="status">
                    Merged into <Link to={`/tickets/${t.merged_into.id}`}>{t.merged_into.label} {t.merged_into.subject}</Link>. Its replies and files are there now.
                  </div>
                )}
                <div className="card ticket-message ticket-message-original">
                  <div className="lbl">
                    {t.requester.display_name}, via {SOURCE_LABEL[t.source].toLowerCase()}, {when(t.created_at)}
                  </div>
                  <p className="body-text">{t.description || <span className="muted">No details given.</span>}</p>
                  <AttachedLine files={filesFor(null)} />
                </div>
                {comments.data?.map((c) => (
                  <div key={c.id} className={`card ticket-message${c.is_internal ? " note ticket-message-internal" : ""}`}>
                    <div className="lbl">
                      {c.is_internal ? "Internal note · " : c.author.id !== t.requester.id ? (events.data?.some(e => e.data.teams_comment_id === c.id) ? "Reply, sent via Teams · " : c.emailed ? "Reply, emailed · " : "Reply, portal only · ") : ""}
                      {c.author.display_name}, via {SOURCE_LABEL[c.source].toLowerCase()}, {when(c.created_at)}
                    </div>
                    <p className="body-text">{c.body}</p>
                    <AttachedLine files={filesFor(c.id)} />
                  </div>
                ))}
                <Composer ticketId={t.id} onDone={refresh} draft={composerDraft} />
                <TeamsReply key={t.id} ticketId={t.id} onDone={refresh} />
              </div>
            ),
          },
          attachments: {
            label: "Attachments",
            node: (
              <>
                <AttachmentsSection
                  files={files.data ?? []}
                  comments={comments.data ?? []}
                  describeAuthor={(uid, fallback) => (uid === me.id ? "you" : (fallback ?? names.user(uid)))}
                  onDropFiles={(list) => dropped.mutate(list)}
                />
                {dropped.error && (
                  <p className="error" role="alert">
                    {dropped.error.message}
                  </p>
                )}
              </>
            ),
          },
          children: { label: "Child tickets", node: <ChildTickets ticket={t} onChanged={refresh} /> },
          checklists: { label: "Checklists", node: <ChecklistsCard ticket={t} onChanged={refresh} /> },
          expenses: { label: "Expenses", node: <ExpensesCard ticket={t} /> },
          time: { label: "Time", node: <TimeCard ticketId={t.id} /> },
          ad_account: { label: "AD account", node: <ADAccountCard ticket={t} onChanged={refresh} /> },
          scripts: { label: "Scripts", node: <ScriptsCard ticket={t} onChanged={refresh} /> },
          history: {
            label: "History",
            node: (
              <div className="card">
                <div className="lbl">History</div>
                <ol className="history">
                  {events.data?.map((e) => (
                    <li key={e.id}>
                      <span className="muted">{when(e.created_at)}</span>
                      <span>
                        {describe(e, names)}{" "}
                        <span className="muted">· {e.data.rule ? `rule "${String(e.data.rule)}"` : e.actor ? names.user(e.actor.id) : "System"}</span>
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            ),
          },
          assignment: { label: "Assignment", node: <AssignmentCard ticket={t} onChanged={refresh} /> },
          requester: { label: "Requester and contact", node: <RequesterCard ticket={t} onChanged={refresh} /> },
          computer: {
            label: "Requester's computer",
            node: (
              <ComputerCard
                ticket={t}
                onChanged={refresh}
                onRunScript={() => {
                  if (drawerMode && !arrangement.editing) { openTool("scripts"); return; }
                  const section = document.querySelector<HTMLElement>("[aria-label='Scripts']");
                  section?.scrollIntoView({ behavior: "smooth", block: "start" });
                  section?.querySelector<HTMLSelectElement>("select")?.focus();
                }}
              />
            ),
          },
          devices: { label: "Devices", node: <DevicesCard ticket={t} onChanged={refresh} /> },
          systems: { label: "Systems", node: <SystemsCard ticket={t} onChanged={refresh} /> },
          vms: { label: "Virtual machines", node: uses(signedIn.data ?? {}, "nutanix") ? <TicketVmsCard ticketId={t.id} onChanged={refresh} /> : null },
          changes: { label: "Changes", node: <ChangesCard ticket={t} /> },
          knowledge: { label: "Knowledge base", node: <KnowledgeCard ticket={t} /> },
          copilot: { label: "Copilot", node: uses(signedIn.data ?? {}, "copilot") ? <CopilotCard ticket={t} onChanged={refresh} /> : null },
          guest_wifi: { label: "Guest Wi-Fi", node: uses(signedIn.data ?? {}, "unifi_vouchers") && can(signedIn.data ?? me, "unifi_vouchers") ? <GuestWifiCard ticket={t} onChanged={refresh} /> : null },
          status: { label: "Status", node: <StatusCard ticket={t} onChanged={refresh} /> },
          actions: { label: "Actions", node: <ActionsCard ticket={t} onChanged={refresh} /> },
          network: { label: "Network context", node: (!hidden.has("computer") || !hidden.has("devices")) ? <TicketNetworkContext ticket={t} includeComputer={!hidden.has("computer")} includeDevices={!hidden.has("devices")} /> : null },
  };

  return (
    <div className={`ticket${drawerMode && !arrangement.editing ? " ticket-drawer-page" : ""}`}>
      <div className="head ticket-workspace-head" ref={measureHead}>
        <nav className="ticket-breadcrumb" aria-label="Ticket breadcrumb">
          <Link to="/">Tickets</Link>
          <span aria-hidden="true">/</span>
          {t.parent && (
            <>
              <Link to={`/tickets/${t.parent.id}`} title={t.parent.subject}>{t.parent.label}</Link>
              <span aria-hidden="true">/</span>
            </>
          )}
          <span aria-current="page">{t.label}</span>
        </nav>
        <div className="ticket-workspace-kicker">SERVICE DESK / TICKET DETAILS</div>
        <div className="row-gap ticket-workspace-title">
          <h1 className="spacer">{t.subject}</h1>
          <PriorityChip priority={t.priority} />
          <SourceChip source={t.source} />
          <StatusChip status={t.status} />
          {drawerMode && !arrangement.editing && <SlaChip ticket={t} long />}
          {drawerMode && !arrangement.editing && ["status", "assignment", "requester", "actions"].some(id => !hidden.has(id)) && <button className="btn2" type="button" onClick={() => openTool("details")}>Status &amp; assignment</button>}
          {!arrangement.editing && (
            <button className="linkish small" type="button" data-arrange-page="ticket" onClick={() => arrangement.setEditing(true)}>
              Arrange this page
            </button>
          )}
        </div>
        <div className="ticket-workspace-meta">
          <span>Requested by <strong>{t.requester.display_name}</strong></span>
          <span>Queue <strong>{t.queue.name}</strong></span>
          <span>Assigned to <strong>{t.assignee?.display_name ?? "Unassigned"}</strong></span>
          <span>Created {when(t.created_at)}</span>
        </div>
      </div>

      {drawerMode && <div className="ticket-drawer-live" hidden={arrangement.editing}>
        <TicketToolsWorkspace me={activeMe} panels={panels} layout={effectiveLayout} request={toolRequest} />
      </div>}
      {(!drawerMode || arrangement.editing) && <ArrangedColumns
        page="ticket"
        editorOnly={drawerMode}
        me={me}
        editing={arrangement.editing}
        onDone={() => arrangement.setEditing(false)}
        layout={arrangement.layout?.effective ?? USUAL_LAYOUT}
        required={arrangement.layout?.required ?? ["conversation"]}
        hasMine={!!arrangement.layout?.mine}
        panels={panels}
      />}
    </div>
  );
}
