# SPDX-License-Identifier: AGPL-3.0-or-later
"""Priority from what the requester tells us: business impact x urgency."""

from sqlalchemy.orm import Session

from app.models import Impact, Priority, PriorityMatrixCell, Urgency

# Used for any combination missing from the editable table. Same values the migration seeds.
DEFAULT_MATRIX: dict[tuple[Impact, Urgency], Priority] = {
    (Impact.single, Urgency.low): Priority.low,
    (Impact.single, Urgency.medium): Priority.low,
    (Impact.single, Urgency.high): Priority.medium,
    (Impact.many, Urgency.low): Priority.medium,
    (Impact.many, Urgency.medium): Priority.medium,
    (Impact.many, Urgency.high): Priority.high,
    (Impact.business, Urgency.low): Priority.medium,
    (Impact.business, Urgency.medium): Priority.high,
    (Impact.business, Urgency.high): Priority.critical,
}


def priority_for(session: Session, impact: Impact, urgency: Urgency) -> Priority:
    cell = session.get(PriorityMatrixCell, (impact, urgency))
    return cell.priority if cell is not None else DEFAULT_MATRIX[(impact, urgency)]


def matrix(session: Session) -> dict[tuple[Impact, Urgency], Priority]:
    current = dict(DEFAULT_MATRIX)
    for cell in session.query(PriorityMatrixCell):
        current[(cell.impact, cell.urgency)] = cell.priority
    return current
