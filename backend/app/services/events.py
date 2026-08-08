"""Process event emission.

``ProcessEvent`` is append-only: never updated, never deleted (TRD §10). Events are
written with the same session as the change they describe, so a transition and its
record commit together or not at all.

Phase 6 uses this for the one event its acceptance criteria call for — an operator
override. **Phase 9 extends emission to every transition** and adds the event log API,
the process-mining CSV export, and bottleneck detection on top of it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlmodel import Session

from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Activity, ProcessEvent

logger = get_logger(__name__)

SYSTEM_ACTOR = "system"


def emit_event(
    session: Session,
    case_id: uuid.UUID,
    activity: Activity | str,
    resource: str = SYSTEM_ACTOR,
    metadata: dict[str, Any] | None = None,
) -> ProcessEvent:
    """Append one event to the log. Does not commit — the caller owns the transaction."""
    name = activity.value if isinstance(activity, Activity) else str(activity)

    event = ProcessEvent(
        case_id=case_id,
        activity=name,
        resource=resource,
        timestamp=utcnow(),
        event_metadata=metadata or {},
    )
    session.add(event)

    logger.info(
        "process event",
        extra={"case_id": str(case_id), "activity": name, "resource": resource},
    )
    return event
