// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
function setup(extra:Record<string,unknown>={}){
 let current=ticket();
 const calls=mockApi({"GET /api/auth/me":ME,"GET /api/tickets/4821":()=>current,"GET /api/tickets/4821/comments":[],"GET /api/tickets/4821/events":[],"GET /api/tickets/4821/attachments":[],
 "GET /api/queues":[{id:2,name:"Network",is_active:true},{id:3,name:"Service desk",is_active:true}],"GET /api/users":[],
 "GET /api/queues/2/assignable":[{id:8,display_name:"Example Technician"}],"GET /api/queues/3/assignable":[],
 "GET /api/field-options":({path}:Call)=>path.includes("resolution_type")?[{id:1,label:"Fixed"}]:path.includes("closure_reason")?[{id:2,label:"Resolved"}]:[],
 "PATCH /api/tickets/4821":({body}:Call)=>(current={...current,...body as object}),
 "POST /api/tickets/4821/assign":()=>current,
 ...extra});
 renderAt("/tickets/4821",[{path:"/tickets/:ticketId",element:<TicketPage me={ME}/>}]);return calls;
}
test("core actions are visible on the conversation tab and assignment opens without changing tabs",async()=>{
 const calls=setup();await screen.findByLabelText("Work notes and replies");
 const actions=screen.getByRole("group",{name:"Ticket actions"});
 expect(within(actions).getByRole("button",{name:"Edit ticket"})).toBeInTheDocument();
 await userEvent.click(within(actions).getByRole("button",{name:"Assign ticket"}));
 const dialog=await screen.findByRole("dialog",{name:"Assign ticket"});
 expect(screen.getByRole("tab",{name:"Conversation"})).toHaveAttribute("aria-selected","true");
 await userEvent.selectOptions(await within(dialog).findByLabelText("Assignee"),"8");
 await userEvent.click(within(dialog).getByRole("button",{name:"Apply assignment"}));
 await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
 expect(calls.find(c=>c.path.endsWith("/assign")&&c.method==="POST")?.body).toMatchObject({assignee_id:8,unassign:false});
});
test("close requires configured resolution fields and does not send the pending draft",async()=>{
 const calls=setup();await userEvent.type(await screen.findByLabelText("Work notes and replies"),"Keep this draft");
 await userEvent.click(screen.getByRole("button",{name:"Close ticket…"}));
 const dialog=await screen.findByRole("dialog",{name:"Close or resolve ticket"});
 expect(within(dialog).getByRole("button",{name:"Close ticket"})).toBeDisabled();
 await userEvent.selectOptions(await within(dialog).findByLabelText("Resolution type"),"1");
 await userEvent.selectOptions(within(dialog).getByLabelText("Closure reason"),"2");
 await userEvent.click(within(dialog).getByRole("button",{name:"Close ticket"}));
 await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
 expect(calls.find(c=>c.method==="PATCH")?.body).toEqual({status:"closed",resolution_type_id:1,closure_reason_id:2});
 expect(calls.filter(c=>c.path.endsWith("/comments")&&c.method==="POST")).toHaveLength(0);
 expect(screen.getByLabelText("Work notes and replies")).toHaveValue("Keep this draft");
 expect(screen.getByRole("button",{name:"Reopen ticket"})).toBeInTheDocument();
});
test("canceling the close dialog makes no changes",async()=>{
 const calls=setup();await screen.findByLabelText("Work notes and replies");
 await userEvent.click(screen.getByRole("button",{name:"Close ticket…"}));
 const dialog=await screen.findByRole("dialog");await userEvent.click(within(dialog).getByRole("button",{name:"Cancel"}));
 expect(calls.filter(c=>["POST","PATCH","DELETE"].includes(c.method))).toHaveLength(0);
});
test("cancel edits clears unsaved details and notes without server writes",async()=>{
 const calls=setup();vi.spyOn(window,"confirm").mockReturnValue(true);
 const note=await screen.findByLabelText("Work notes and replies");await userEvent.type(note,"Discard me");
 await userEvent.click(screen.getByRole("button",{name:"Edit ticket"}));
 const subject=await screen.findByLabelText("Subject");await userEvent.type(subject," revised");
 await userEvent.click(screen.getByRole("button",{name:"Cancel edits"}));
 expect(note).toHaveValue("");expect(subject).toHaveValue("Switch port flapping, 2nd floor");
 expect(calls.filter(c=>["POST","PATCH","DELETE"].includes(c.method))).toHaveLength(0);
});
test("canceling discard preserves the draft",async()=>{
 setup();vi.spyOn(window,"confirm").mockReturnValue(false);
 const note=await screen.findByLabelText("Work notes and replies");await userEvent.type(note,"Keep me");
 await userEvent.click(screen.getByRole("button",{name:"Cancel edits"}));expect(note).toHaveValue("Keep me");
});
