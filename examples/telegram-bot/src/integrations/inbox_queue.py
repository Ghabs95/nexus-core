"""PostgreSQL-backed inbox queue for task ingestion.

Used by the example Telegram bot when ``NEXUS_INBOX_BACKEND=postgres``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from config import NEXUS_STORAGE_DSN

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False


logger = logging.getLogger(__name__)


def _require_sqlalchemy() -> None:
    if not _SA_AVAILABLE:
        raise ImportError(
            "sqlalchemy and psycopg2-binary are required for Postgres inbox queue. "
            "Install with: pip install nexus-core[postgres]"
        )


def _normalize_dsn(raw_dsn: str) -> str:
    dsn = str(raw_dsn or "").strip()
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+psycopg2://", 1)
    return dsn


if _SA_AVAILABLE:

    class _InboxBase(DeclarativeBase):
        pass

    class _InboxTaskRow(_InboxBase):
        __tablename__ = "nexus_inbox_tasks"

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(
            sa.Integer, primary_key=True, autoincrement=True
        )
        project_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False)
        workspace: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256), nullable=False)
        filename: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256), nullable=False)
        markdown_content: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text, nullable=False)
        status: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(32), nullable=False, default="pending"
        )
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(tz=UTC),
        )
        claimed_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        processed_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        claimed_by: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(256))
        error: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)


@dataclass
class InboxQueueTask:
    id: int
    project_key: str
    workspace: str
    filename: str
    markdown_content: str


_ENGINE = None


def _get_engine():
    global _ENGINE
    _require_sqlalchemy()

    if _ENGINE is not None:
        return _ENGINE

    dsn = _normalize_dsn(NEXUS_STORAGE_DSN)
    if not dsn:
        raise ValueError("NEXUS_STORAGE_DSN is required for postgres inbox backend")

    _ENGINE = sa.create_engine(dsn, pool_size=5)
    _InboxBase.metadata.create_all(_ENGINE)
    logger.info("Postgres inbox queue initialized")
    return _ENGINE


def enqueue_task(
    *,
    project_key: str,
    workspace: str,
    filename: str,
    markdown_content: str,
) -> int:
    """Insert a task into postgres inbox queue and return row id."""
    engine = _get_engine()
    with Session(engine) as session:
        row = _InboxTaskRow(
            project_key=str(project_key),
            workspace=str(workspace),
            filename=str(filename),
            markdown_content=str(markdown_content),
            status="pending",
            created_at=datetime.now(tz=UTC),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def claim_pending_tasks(*, limit: int, worker_id: str) -> list[InboxQueueTask]:
    """Claim pending tasks and return them for processing."""
    engine = _get_engine()
    claimed: list[InboxQueueTask] = []
    now = datetime.now(tz=UTC)

    with Session(engine) as session:
        query = (
            session.query(_InboxTaskRow)
            .filter(_InboxTaskRow.status == "pending")
            .order_by(_InboxTaskRow.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        )
        rows = query.all()
        if not rows:
            session.commit()
            return []

        for row in rows:
            row.status = "processing"
            row.claimed_by = worker_id
            row.claimed_at = now
            row.error = None
            claimed.append(
                InboxQueueTask(
                    id=int(row.id),
                    project_key=row.project_key,
                    workspace=row.workspace,
                    filename=row.filename,
                    markdown_content=row.markdown_content,
                )
            )
        session.commit()

    return claimed


def mark_task_done(task_id: int) -> None:
    """Mark a claimed task as done."""
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_InboxTaskRow, int(task_id))
        if not row:
            return
        row.status = "done"
        row.processed_at = datetime.now(tz=UTC)
        row.error = None
        session.commit()


def mark_task_failed(task_id: int, error: str) -> None:
    """Mark a claimed task as failed with error details."""
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_InboxTaskRow, int(task_id))
        if not row:
            return
        row.status = "failed"
        row.error = str(error or "")[:2000]
        row.processed_at = datetime.now(tz=UTC)
        session.commit()


def get_queue_overview(*, limit: int = 10) -> dict[str, Any]:
    """Return queue counts and recent rows for monitoring commands."""
    engine = _get_engine()
    safe_limit = max(1, min(int(limit), 50))
    counts = {
        "pending": 0,
        "processing": 0,
        "done": 0,
        "failed": 0,
    }
    recent: list[dict[str, Any]] = []

    with Session(engine) as session:
        grouped = (
            session.query(_InboxTaskRow.status, sa.func.count(_InboxTaskRow.id))
            .group_by(_InboxTaskRow.status)
            .all()
        )
        for status, count in grouped:
            if status in counts:
                counts[status] = int(count)

        rows = (
            session.query(_InboxTaskRow).order_by(_InboxTaskRow.id.desc()).limit(safe_limit).all()
        )

        for row in rows:
            recent.append(
                {
                    "id": int(row.id),
                    "project_key": row.project_key,
                    "filename": row.filename,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "claimed_by": row.claimed_by,
                    "error": row.error,
                }
            )

    counts["total"] = sum(counts.values())
    return {
        "counts": counts,
        "recent": recent,
        "limit": safe_limit,
    }
