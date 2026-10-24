// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ADGroup } from "../api/client";
import { UserPicker } from "./UserPicker";

/** "Copy groups from…": another person's groups as a starting point (a new starter like a colleague).
 * Only groups technicians may change are copied; the rest are counted, not added. */
export function CopyGroups({ onCopy }: { onCopy: (groups: ADGroup[]) => void }) {
  const [from, setFrom] = useState<{ id: number; display_name: string; email: string } | null>(null);
  const copy = useMutation({
    mutationFn: (userId: number) => api.ad.userGroups(userId),
    onSuccess: (groups) => onCopy(groups.filter((g) => g.changeable)),
  });
  const got = copy.data;
  const skipped = got ? got.filter((g) => !g.changeable).length : 0;
  return (
    <div className="stack" style={{ gap: 4 }}>
      <UserPicker
        label="Copy groups from another person"
        placeholder="Type a colleague's name"
        value={from}
        onChange={(u) => {
          setFrom(u);
          if (u) copy.mutate(u.id);
          else copy.reset();
        }}
      />
      {got && from && (
        <span className="small muted" role="status">
          Copied {got.length - skipped} of {from.display_name}'s {got.length} groups
          {skipped > 0 && ` (${skipped} left out: technicians can't change ${skipped === 1 ? "it" : "them"})`}. Groups they're already in aren't added twice; remove any they shouldn't get.
        </span>
      )}
      {copy.error && <p className="error small">{copy.error.message}</p>}
    </div>
  );
}
