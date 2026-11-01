// SPDX-License-Identifier: AGPL-3.0-or-later
import type { Problem } from "../../api/client";

export const STATUS: Record<Problem["status"], string> = {
  open: "Open",
  investigating: "Investigating",
  known_error: "Known error",
  resolved: "Resolved",
  closed: "Closed",
};
const STATUS_TONE: Record<Problem["status"], string> = {
  open: "n",
  investigating: "a",
  known_error: "b",
  resolved: "g",
  closed: "",
};
export const STATUSES = Object.keys(STATUS) as Problem["status"][];

export function ProblemStatus({ status }: { status: Problem["status"] }) {
  return <span className={`chip ${STATUS_TONE[status]}`}>{STATUS[status]}</span>;
}
