"""Settings > Ticket fields: the dropdown lists (resolution types, closure reasons, departments).
Agents read the active choices; admins edit the lists."""

from fastapi import APIRouter, HTTPException, status

from app import fields
from app.auth import Admin, Agent, SessionDep
from app.models import FieldList, FieldOption
from app.schemas import FieldOptionIn, FieldOptionOut, FieldOptionUpdate

router = APIRouter(prefix="/api/field-options", tags=["ticket fields"])


@router.get("", response_model=list[FieldOptionOut])
def list_options(user: Agent, session: SessionDep, list: FieldList, include_retired: bool = False):  # noqa: A002
    return fields.options(session, list, include_retired=include_retired and user.role == "admin")


@router.post("", response_model=FieldOptionOut, status_code=status.HTTP_201_CREATED)
def add_option(body: FieldOptionIn, _: Admin, session: SessionDep):
    label = body.label.strip()
    existing = fields.options(session, body.list, include_retired=True)
    if any(o.label.lower() == label.lower() for o in existing):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{label} is already on the list")
    option = FieldOption(list=body.list, label=label, position=max((o.position for o in existing), default=0) + 10)
    session.add(option)
    session.commit()
    return option


@router.patch("/{option_id}", response_model=FieldOptionOut)
def update_option(option_id: int, body: FieldOptionUpdate, _: Admin, session: SessionDep):
    option = session.get(FieldOption, option_id)
    if option is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Choice not found")
    if body.label is not None:
        label = body.label.strip()
        if any(o.id != option.id and o.label.lower() == label.lower() for o in fields.options(session, option.list, include_retired=True)):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{label} is already on the list")
        option.label = label
    if body.is_active is not None:
        option.is_active = body.is_active
    session.commit()
    return option


@router.put("/order/{list}", response_model=list[FieldOptionOut])
def reorder(list: FieldList, ids: list[int], _: Admin, session: SessionDep):  # noqa: A002
    options = {o.id: o for o in fields.options(session, list, include_retired=True)}
    if set(ids) != set(options):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give every choice of the list, in the new order")
    for i, option_id in enumerate(ids):
        options[option_id].position = (i + 1) * 10
    session.commit()
    return fields.options(session, list, include_retired=True)


@router.post("/departments/import", response_model=list[FieldOptionOut])
def import_departments(_: Admin, session: SessionDep):
    """Add every department on active (AD) users that isn't on the list yet."""
    fields.import_departments(session)
    session.commit()
    return fields.options(session, FieldList.department, include_retired=True)
