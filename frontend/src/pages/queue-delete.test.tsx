// SPDX-License-Identifier: AGPL-3.0-or-later
import {screen,waitFor,within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {ME,mockApi,renderAt,ticket,type Call} from "../test/util";
import {QueuePage} from "./QueuePage";

afterEach(()=>vi.unstubAllGlobals());
function setup(permissions:string[],extra:Record<string,unknown>={}){
 const rows=[ticket({id:4821}),ticket({id:4822})];
 const calls=mockApi({"GET /api/tickets":()=>({items:rows,total:rows.length}),"GET /api/tickets/views":{},"GET /api/queues":[],"GET /api/users":[],
 "DELETE /api/tickets/4821":()=>{rows.splice(rows.findIndex(t=>t.id===4821),1);return null;},
 "DELETE /api/tickets/4822":()=>{rows.splice(rows.findIndex(t=>t.id===4822),1);return null;},...extra});
 renderAt("/",[{path:"/",element:<QueuePage me={{...ME,permissions}}/>}]);return calls;
}
test("bulk editing alone never grants Delete",async()=>{
 setup(["ticket_bulk"]);await userEvent.click(await screen.findByLabelText("Select #4821"));
 expect(screen.getByLabelText("Move to queue")).toBeInTheDocument();expect(screen.queryByRole("button",{name:"Delete selected…"})).toBeNull();
});
test("delete permission alone permits selecting tickets but not reassignment",async()=>{
 const calls=setup(["ticket_delete"]);await userEvent.click(await screen.findByLabelText("Select #4821"));
 await userEvent.click(screen.getByLabelText("Select #4822"));
 expect(screen.queryByLabelText("Move to queue")).toBeNull();
 await userEvent.click(screen.getByRole("button",{name:"Delete selected…"}));
 const dialog=await screen.findByRole("dialog",{name:"Delete 2 tickets?"});
 expect(within(dialog).getByRole("button",{name:"Delete permanently"})).toBeDisabled();
 expect(calls.filter(c=>c.method==="DELETE")).toHaveLength(0);
 await userEvent.type(within(dialog).getByLabelText("Type DELETE 2 to confirm"),"DELETE 2");
 await userEvent.click(within(dialog).getByRole("button",{name:"Delete permanently"}));
 await waitFor(()=>expect(calls.filter(c=>c.method==="DELETE")).toHaveLength(2));
 await screen.findByText(/2 deleted/);
});
test("canceling deletion sends no DELETE requests",async()=>{
 const calls=setup(["ticket_delete"]);await userEvent.click(await screen.findByLabelText("Select #4821"));
 await userEvent.click(screen.getByRole("button",{name:"Delete selected…"}));
 await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button",{name:"Cancel"}));
 expect(calls.filter(c=>c.method==="DELETE")).toHaveLength(0);
});
test("permission revocation stops the batch and preserves the failed selection",async()=>{
 const calls=setup(["ticket_delete"],{"DELETE /api/tickets/4821":new Response(JSON.stringify({detail:"Permission revoked"}),{status:403})});
 await userEvent.click(await screen.findByLabelText("Select #4821"));await userEvent.click(screen.getByLabelText("Select #4822"));
 await userEvent.click(screen.getByRole("button",{name:"Delete selected…"}));const dialog=await screen.findByRole("dialog");
 await userEvent.type(within(dialog).getByLabelText("Type DELETE 2 to confirm"),"DELETE 2");
 await userEvent.click(within(dialog).getByRole("button",{name:"Delete permanently"}));
 expect(await within(dialog).findByRole("alert")).toHaveTextContent("Permission revoked");
 expect(calls.filter((c:Call)=>c.method==="DELETE").map(c=>c.path)).toEqual(["/api/tickets/4821"]);
 expect(screen.getByLabelText("Select #4821")).toBeChecked();
});

test("partial success removes only deleted tickets from selection",async()=>{
 setup(["ticket_delete"],{"DELETE /api/tickets/4822":new Response(JSON.stringify({detail:"Ticket is no longer accessible"}),{status:404})});
 await userEvent.click(await screen.findByLabelText("Select #4821"));await userEvent.click(screen.getByLabelText("Select #4822"));
 await userEvent.click(screen.getByRole("button",{name:"Delete selected…"}));const dialog=await screen.findByRole("dialog");
 await userEvent.type(within(dialog).getByLabelText("Type DELETE 2 to confirm"),"DELETE 2");
 await userEvent.click(within(dialog).getByRole("button",{name:"Delete permanently"}));
 expect(await within(dialog).findByRole("status")).toHaveTextContent("1 deleted");
 await waitFor(()=>expect(screen.queryByLabelText("Select #4821")).toBeNull());
 expect(screen.getByLabelText("Select #4822")).toBeChecked();
});
