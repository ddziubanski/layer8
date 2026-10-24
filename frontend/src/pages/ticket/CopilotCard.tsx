import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, type Ticket } from "../../api/client";
import { CopilotSession } from "./CopilotSession";

export function CopilotCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const connection = useQuery({ queryKey: ["teams-connection"], queryFn: api.teams.connection });
  if (connection.error) return <p className="error" role="alert">{connection.error.message}</p>;
  if (!connection.data) return null;
  if (!connection.data.connected || !connection.data.copilot) return <p className="small" style={{ margin: 0 }}>
    <Link to="/preferences">{connection.data.connected ? "Reconnect" : "Connect"} your Microsoft account in My preferences</Link> to ask Copilot. You need a Microsoft 365 Copilot licence.
  </p>;
  const account = connection.data.account ?? "your Microsoft work account";
  return <CopilotSession key={`${ticket.id}:${account}`} ticket={ticket} account={account} onChanged={onChanged} />;
}
