"""Device lifecycle: warranties ending, devices due for replacement, and a forecast of how many
come due each quarter (for budgeting). Flags only."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Device


def replace_due(d: Device) -> date | None:
    """Its own replace-by date, else purchase date plus the lifetime in Settings."""
    if d.replace_by:
        return d.replace_by
    if d.purchased_on:
        try:
            return d.purchased_on.replace(year=d.purchased_on.year + settings.device_life_years)
        except ValueError:  # 29 February
            return d.purchased_on.replace(year=d.purchased_on.year + settings.device_life_years, day=28)
    return None


def quarter(d: date) -> str:
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


@dataclass
class Report:
    warranty_soon: list[Device]
    warranty_ended: list[Device]
    replace_soon: list[tuple[Device, date]]  # due within a year, or overdue
    forecast: list[tuple[str, int]]  # the next 12 quarters (this one first), devices due in each
    overdue: int
    unknown: int  # no purchase or replace-by date


def report(session: Session, today: date | None = None) -> Report:
    today = today or date.today()
    devices = list(session.scalars(select(Device).where(Device.is_active).order_by(Device.name)))
    warn = today + timedelta(days=settings.warranty_warn_days)
    soon = [d for d in devices if d.warranty_ends and today <= d.warranty_ends <= warn]
    ended = [d for d in devices if d.warranty_ends and d.warranty_ends < today]
    due = [(d, replace_due(d)) for d in devices]
    within_year = sorted(((d, r) for d, r in due if r and r <= today + timedelta(days=365)), key=lambda x: x[1])
    quarters, q = [], date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    for _ in range(12):
        quarters.append(quarter(q))
        q = date(q.year + (q.month + 2) // 12, (q.month + 2) % 12 + 1, 1)
    counts = dict.fromkeys(quarters, 0)
    overdue = 0
    for _, r in due:
        if r is None:
            continue
        if r < date(today.year, (today.month - 1) // 3 * 3 + 1, 1):
            overdue += 1
        elif quarter(r) in counts:
            counts[quarter(r)] += 1
    return Report(
        warranty_soon=sorted(soon, key=lambda d: d.warranty_ends), warranty_ended=sorted(ended, key=lambda d: d.warranty_ends),
        replace_soon=within_year, forecast=list(counts.items()), overdue=overdue, unknown=sum(1 for _, r in due if r is None),
    )
