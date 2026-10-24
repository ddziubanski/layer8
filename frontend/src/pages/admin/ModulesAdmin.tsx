// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQueryClient } from "@tanstack/react-query";
import type { Me } from "../../api/client";
import { ME_KEY } from "../../queryClient";
import { SettingsForm } from "./SettingsForm";

export function ModulesAdmin({ me }: { me: Me }) {
  const client = useQueryClient();
  return <div className="stack">
    <div className="card stack">
      <h1>Global modules</h1>
      <p>Keep Layer 8 focused on your service desk. Choose which workspace areas your organization uses; technicians can hide more in My preferences.</p>
      <p className="muted">Tickets, My preferences and sign-out stay available. Admins retain Settings access, and requester self-service remains part of the core helpdesk.</p>
      <p className="muted">Turning a workspace module off removes its navigation and console pages. Existing data, ticket context and API permissions are retained. Background jobs and integrations use their own settings.</p>
    </div>
    <SettingsForm me={me} section="modules" title="Available workspace modules" embedded
      intro="Changes apply to every account. Personal preferences cannot restore a module turned off here. Save to apply; other signed-in clients refresh within 30 seconds or when they regain focus."
      onSaved={() => { void client.invalidateQueries({ queryKey: ME_KEY }); }} />
  </div>;
}
