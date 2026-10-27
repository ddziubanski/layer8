// SPDX-License-Identifier: AGPL-3.0-or-later
import type { Device, NetworkTarget } from "../api/client";

export const networkTargetKey = (target: NetworkTarget) => JSON.stringify([target.kind, target.source_id, target.key]);
export const deviceSource = (device: Pick<Device, "source">) => device.source === "directory" ? "Active Directory" : "Manual";
export function DeviceSource({ label }: { label: string }) {
  return <span className="chip n" title={`Source: ${label}`}>{label}</span>;
}
