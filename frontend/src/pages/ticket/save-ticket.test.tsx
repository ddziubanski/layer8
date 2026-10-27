// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, type Ticket } from "../../api/client";
import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup(extra: Record<string, unknown> = {}) {
  let current = ticket();
  const queues = [{id:2,name:"Network"},{id:3,name:"Service desk"}];
  const calls = mockApi({
    "GET /api/auth/me": ME,
    "GET /api/tickets/4821": () => current,
    "GET /api/tickets/4821/comments": [], "GET /api/tickets/4821/attachments": [], "GET /api/tickets/4821/events": [],
    "GET /api/queues": queues, "GET /api/users": [], "GET /api/queues/2/assignable": [],
    "GET /api/canned-replies": [],
    "GET /api/field-options": ({path}: Call) => path.includes("resolution_type") ? [{id:1,label:"Fixed"}] : path.includes("closure_reason") ? [{id:2,label:"Resolved"}] : [],
    "PATCH /api/tickets/4821": ({body}: Call) => (current = {...current,...body as object}),
    "POST /api/tickets/4821/assign": ({body}: Call) => (current = {...current,queue:queues.find(q=>q.id===(body as {queue_id:number}).queue_id)!}),
    "POST /api/tickets/4821/comments": {id:11},
    "POST /api/tickets/4821/time": {id:12},
    "POST /api/tickets/4821/attachments": {id:"file-1"},
    ...extra,
  });
  renderAt("/tickets/4821",[{path:"/tickets/:ticketId",element:<TicketPage me={ME}/>}]);
  return calls;
}

test("Save ticket persists details and an internal work note together, across tabs", async () => {
  const calls = setup();
  const note = await screen.findByLabelText("Work notes and replies");
  expect(screen.getByRole("button",{name:"Save ticket"})).toBeDisabled();
  await userEvent.type(note,"Replaced the faulty cable.");
  await userEvent.click(screen.getByRole("tab",{name:"Details"}));
  const subject = await screen.findByLabelText("Subject");
  await userEvent.clear(subject); await userEvent.type(subject,"Cable fault resolved");
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(note).toHaveValue(""));
  const writes = calls.filter(c=>c.method==="PATCH"||c.method==="POST");
  expect(writes.map(c=>c.path)).toEqual(["/api/tickets/4821","/api/tickets/4821/comments"]);
  expect(writes[0].body).toEqual({subject:"Cable fault resolved"});
  expect(writes[1].body).toEqual({body:"Replaced the faulty cable.",is_internal:true,email_requester:false});
  expect(subject).toHaveValue("Cable fault resolved");
  expect(screen.getByRole("button",{name:"Save ticket"})).toBeDisabled();
});

test("Save ticket honors requester email versus portal-only delivery", async () => {
  const calls = setup();
  const note = await screen.findByLabelText("Work notes and replies");
  await userEvent.type(note,"Please test the connection.");
  await userEvent.click(screen.getByLabelText("Show to the requester in the portal"));
  expect(screen.getByText(/Pending: Email requester/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(note).toHaveValue(""));
  expect(calls.find(c=>c.path.endsWith("/comments")&&c.method==="POST")?.body).toEqual({body:"Please test the connection.",is_internal:false,email_requester:true});
  await userEvent.type(note,"Portal update only.");
  await userEvent.click(screen.getByLabelText("Show to the requester in the portal"));
  await userEvent.click(screen.getByLabelText("Email it to them too"));
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(note).toHaveValue(""));
  expect(calls.filter(c=>c.path.endsWith("/comments")&&c.method==="POST").at(-1)?.body).toEqual({body:"Portal update only.",is_internal:false,email_requester:false});
});

test("all required fields are checked before changes or replies are sent", async () => {
  const calls = setup();
  await userEvent.type(await screen.findByLabelText("Work notes and replies"),"Keep this draft");
  await userEvent.click(screen.getByRole("tab",{name:"Details"}));
  await waitFor(()=>expect(calls.some(c=>c.path.includes("closure_reason"))).toBe(true));
  await userEvent.selectOptions(screen.getByLabelText("Status"),"resolved");
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a resolution type");
  expect(calls.filter(c=>["POST","PATCH"].includes(c.method))).toHaveLength(0);
  const form=screen.getByRole("form",{name:"Resolution"});
  await userEvent.selectOptions(within(form).getByLabelText("Resolution type"),"1");
  await userEvent.selectOptions(within(form).getByLabelText("Closure reason"),"2");
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(calls.some(c=>c.path.endsWith("/comments")&&c.method==="POST")).toBe(true));
});

test("a failed detail save retains drafts and does not send the pending note", async () => {
  const calls=setup({"PATCH /api/tickets/4821":new Response(JSON.stringify({detail:"Ticket update refused"}),{status:409})});
  const note=await screen.findByLabelText("Work notes and replies");
  await userEvent.type(note,"Do not lose me");
  await userEvent.click(screen.getByRole("tab",{name:"Details"}));
  await userEvent.type(await screen.findByLabelText("Subject")," revised");
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(screen.getAllByRole("alert").some(a=>a.textContent?.includes("Remaining changes are kept"))).toBe(true));
  expect(note).toHaveValue("Do not lose me");
  expect(calls.filter(c=>c.method==="POST")).toHaveLength(0);
});

test("Save ticket includes pending queue moves and contact changes before the note", async () => {
  const calls=setup();
  await userEvent.type(await screen.findByLabelText("Work notes and replies"),"Moved to service desk");
  await userEvent.click(screen.getByRole("tab",{name:"Details"}));
  await screen.findByRole("option",{name:"Service desk"});
  await userEvent.selectOptions(screen.getByLabelText("Queue"),"3");
  await userEvent.type(screen.getByLabelText(/Reason for moving/),"Hardware follow-up");
  await userEvent.type(screen.getByLabelText("Contact phone"),"ext. 4410");
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(calls.some(c=>c.method==="POST"&&c.path.endsWith("/comments"))).toBe(true));
  const writes=calls.filter(c=>["POST","PATCH"].includes(c.method));
  expect(writes.map(c=>c.path)).toEqual(["/api/tickets/4821/assign","/api/tickets/4821","/api/tickets/4821/comments"]);
  expect(writes[0].body).toEqual({queue_id:3,unassign:false,note:"Hardware follow-up"});
  expect(writes[1].body).toEqual({contact_phone:"ext. 4410"});
});

test("double clicking Save cannot duplicate a request while saving", async () => {
  setup(); let finish!: (value:Ticket)=>void;
  const update=vi.spyOn(api,"updateTicket").mockImplementation(()=>new Promise<Ticket>(resolve=>{finish=resolve;}));
  await screen.findByLabelText("Work notes and replies");
  await userEvent.click(screen.getByRole("tab",{name:"Details"}));
  await userEvent.type(await screen.findByLabelText("Subject")," changed");
  await userEvent.dblClick(screen.getByRole("button",{name:"Save ticket"}));
  expect(update).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button",{name:"Saving ticket…"})).toBeDisabled();
  finish(ticket({subject:"Saved title"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"Save ticket"})).toBeDisabled());
});

test("retrying an attachment failure does not post the comment twice", async () => {
  let uploads=0;
  const calls=setup({"POST /api/tickets/4821/attachments":()=>++uploads===1?new Response(JSON.stringify({detail:"Temporary upload failure"}),{status:503}):{id:"ok"}});
  const note=await screen.findByLabelText("Work notes and replies");
  await userEvent.type(note,"See attachment");
  await userEvent.upload(screen.getByTestId("file-input"),new File(["x"],"example.txt",{type:"text/plain"}));
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  expect(await screen.findByRole("button",{name:"Retry remaining items"})).toBeEnabled();
  await userEvent.click(screen.getByRole("button",{name:"Save ticket"}));
  await waitFor(()=>expect(note).toHaveValue(""));
  expect(calls.filter(c=>c.method==="POST"&&c.path.endsWith("/comments"))).toHaveLength(1);
  expect(uploads).toBe(2);
});
