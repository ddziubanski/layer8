// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, type AssignIn, type Me, type Ticket, type TicketUpdate } from "../../api/client";
import { ActionDialog } from "../../components/ActionDialog";
import { DeleteTickets } from "../../components/DeleteTickets";
import { can } from "../../permissions";
import { TicketSaveButton, useTicketSaveState, useTicketSaveTask } from "./TicketSave";

export function TicketCoreActions({ ticket, me, onEdit, onChanged }: { ticket: Ticket; me: Me; onEdit: () => void; onChanged: () => void }) {
  const [panel, setPanel] = useState<"assign" | "close" | null>(null);
  const save = useTicketSaveState();
  const navigate = useNavigate();
  const client = useQueryClient();
  const reopened = useMutation({ mutationFn: () => api.updateTicket(ticket.id, {status:"open"}), onSuccess: updated => client.setQueryData(["ticket",ticket.id],updated), onSettled:onChanged });
  useTicketSaveTask("reopen", {dirty:false,busy:reopened.isPending,label:"Reopen",error:reopened.error?.message,save:async()=>{}});
  const closed = ticket.status === "closed" || ticket.status === "resolved";
  return <div className="ticket-core-actions" role="group" aria-label="Ticket actions">
    <TicketSaveButton />
    <button className="btn2" type="button" disabled={save?.busy} onClick={onEdit}>Edit ticket</button>
    <button className="btn2" type="button" disabled={save?.busy || save?.dirtyIds.includes("assignment")} title={save?.dirtyIds.includes("assignment") ? "Save or cancel the pending queue move first" : undefined} onClick={()=>setPanel("assign")}>Assign ticket</button>
    {closed ? <button className="btn2" type="button" disabled={save?.busy} onClick={()=>reopened.mutate()}>Reopen ticket</button>
      : <button className="btn2" type="button" disabled={save?.busy || save?.dirtyIds.includes("status")} title={save?.dirtyIds.includes("status") ? "Save or cancel the pending resolution first" : undefined} onClick={()=>setPanel("close")}>Close ticket…</button>}
    {!!save?.dirtyIds.length && <button className="btn2" type="button" disabled={save.busy} onClick={()=>{if(window.confirm("Discard unsaved ticket edits and the draft note/reply? Changes already saved will stay.")) save.discard();}}>Cancel edits</button>}
    {can(me,"ticket_delete") && <details className="ticket-actions-more"><summary className="btn2">More</summary><DeleteTickets tickets={[ticket]} label="Delete ticket…" disabled={save?.busy} onComplete={done=>{if(done.length){client.invalidateQueries({queryKey:["tickets"]});client.invalidateQueries({queryKey:["views"]});client.removeQueries({queryKey:["ticket",ticket.id]});navigate("/");}}} /></details>}
    {reopened.error && <p className="error small" role="alert">{reopened.error.message}</p>}
    {panel === "assign" && <AssignTicket ticket={ticket} onClose={()=>setPanel(null)} onChanged={onChanged} />}
    {panel === "close" && <CloseTicket ticket={ticket} onClose={()=>setPanel(null)} onChanged={onChanged} />}
  </div>;
}

function AssignTicket({ticket,onClose,onChanged}:{ticket:Ticket;onClose:()=>void;onChanged:()=>void}) {
  const [queueId,setQueueId]=useState(ticket.queue.id);
  const [assignee,setAssignee]=useState("keep");
  const [note,setNote]=useState("");
  const client=useQueryClient();
  const queues=useQuery({queryKey:["queues"],queryFn:api.queues});
  const owners=useQuery({queryKey:["assignable",queueId],queryFn:()=>api.assignable(queueId)});
  const assign=useMutation({mutationFn:()=>api.assign(ticket.id,{
    ...(queueId!==ticket.queue.id?{queue_id:queueId}:{}),
    ...(assignee==="none"?{unassign:true}:assignee!=="keep"?{assignee_id:Number(assignee),unassign:false}:{}),note:note.trim()||null,
  } as AssignIn),onSuccess:updated=>{client.setQueryData(["ticket",ticket.id],updated);onChanged();onClose();}});
  useTicketSaveTask("quick-assign",{dirty:false,busy:assign.isPending,label:"Assignment",error:assign.error?.message,save:async()=>{}});
  return <ActionDialog title="Assign ticket" busy={assign.isPending} onClose={onClose}>
    <form className="stack" onSubmit={e=>{e.preventDefault();if(!assign.isPending)assign.mutate();}}>
      <label className="field">Queue<select value={queueId} disabled={assign.isPending} onChange={e=>{setQueueId(Number(e.target.value));setAssignee("keep");}}>{(queues.data??[ticket.queue]).filter(q=>!("is_active"in q)||q.is_active||q.id===ticket.queue.id).map(q=><option key={q.id} value={q.id}>{q.name}</option>)}</select></label>
      <label className="field">Assignee<select value={assignee} disabled={assign.isPending||owners.isFetching} onChange={e=>setAssignee(e.target.value)}><option value="keep">Keep current ({ticket.assignee?.display_name??"Unassigned"})</option><option value="none">Unassigned</option>{owners.data?.map(p=><option key={p.id} value={p.id}>{p.display_name}</option>)}</select></label>
      <label className="field">Reason (optional)<textarea rows={2} maxLength={2000} value={note} disabled={assign.isPending} onChange={e=>setNote(e.target.value)}/></label>
      <p className="small muted">Updates assignment only. Pending ticket edits and notes stay unsaved. Moving queues still follows queue ownership rules.</p>
      {(assign.error||queues.error||owners.error)&&<p role="alert" className="error">{assign.error?.message??queues.error?.message??owners.error?.message}</p>}
      <div className="row-gap"><button className="btn2" type="button" disabled={assign.isPending} onClick={onClose}>Cancel</button><button className="btn" type="submit" disabled={assign.isPending||(queueId===ticket.queue.id&&assignee==="keep")||owners.isFetching}>Apply assignment</button></div>
    </form>
  </ActionDialog>;
}

function CloseTicket({ticket,onClose,onChanged}:{ticket:Ticket;onClose:()=>void;onChanged:()=>void}) {
  const [status,setStatus]=useState<"resolved"|"closed">("closed");
  const [resolution,setResolution]=useState(String(ticket.resolution_type?.id??""));
  const [reason,setReason]=useState(String(ticket.closure_reason?.id??""));
  const client=useQueryClient();
  const save=useTicketSaveState();
  const resolutions=useQuery({queryKey:["field-options","resolution_type"],queryFn:()=>api.fieldOptions("resolution_type")});
  const reasons=useQuery({queryKey:["field-options","closure_reason"],queryFn:()=>api.fieldOptions("closure_reason")});
  const update=useMutation({mutationFn:()=>api.updateTicket(ticket.id,{status,resolution_type_id:resolution?Number(resolution):null,closure_reason_id:reason?Number(reason):null} as TicketUpdate),
    onSuccess:updated=>{client.setQueryData(["ticket",ticket.id],updated);onChanged();onClose();}});
  useTicketSaveTask("quick-close",{dirty:false,busy:update.isPending,label:"Close",error:update.error?.message,save:async()=>{}});
  const valid=resolutions.isSuccess&&reasons.isSuccess&&(!resolutions.data.length||!!resolution)&&(!reasons.data.length||!!reason);
  return <ActionDialog title="Close or resolve ticket" busy={update.isPending} onClose={onClose}>
    <form className="stack" onSubmit={e=>{e.preventDefault();if(valid&&!update.isPending)update.mutate();}}>
      <label className="field">Outcome<select value={status} disabled={update.isPending} onChange={e=>setStatus(e.target.value as typeof status)}><option value="closed">Closed</option><option value="resolved">Resolved</option></select></label>
      {!!resolutions.data?.length&&<label className="field">Resolution type<select required value={resolution} disabled={update.isPending} onChange={e=>setResolution(e.target.value)}><option value="">Choose…</option>{resolutions.data.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></label>}
      {!!reasons.data?.length&&<label className="field">Closure reason<select required value={reason} disabled={update.isPending} onChange={e=>setReason(e.target.value)}><option value="">Choose…</option>{reasons.data.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></label>}
      {!!save?.dirtyIds.length&&<div className="note card"><p className="small">There are unsaved edits or a note/reply. Closing changes status only. Use Save ticket first if you want those included.</p><TicketSaveButton/></div>}
      {(update.error||resolutions.error||reasons.error)&&<p className="error" role="alert">{update.error?.message??resolutions.error?.message??reasons.error?.message}</p>}
      <div className="row-gap"><button className="btn2" type="button" disabled={update.isPending||save?.busy} onClick={onClose}>Cancel</button><button className="btn" type="submit" disabled={!valid||update.isPending||save?.busy}>{status==="closed"?"Close ticket":"Resolve ticket"}</button></div>
    </form>
  </ActionDialog>;
}
