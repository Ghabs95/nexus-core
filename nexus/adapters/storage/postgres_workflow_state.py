"""PostgreSQL implementation of :class:`WorkflowStateStore`.

Re-uses the same SQLAlchemy engine/session pattern as
:class:`~nexus.adapters.storage.postgres.PostgreSQLStorageBackend`.

Requires the ``postgres`` optional extra::

    pip install nexus-core[postgres]

Tables:
- ``nexus_workflow_mappings`` — issue → workflow_id
- ``nexus_approval_state``   — pending approval records
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False

logger = logging.getLogger(__name__)


def _require_sqlalchemy() -> None:
    if not _SA_AVAILABLE:
        raise ImportError(
            "sqlalchemy and psycopg2-binary are required for PostgresWorkflowStateStore. "
            "Install them with: pip install nexus-core[postgres]"
        )


# ---------------------------------------------------------------------------
# ORM definitions (only when SQLAlchemy is available)
# ---------------------------------------------------------------------------

if _SA_AVAILABLE:

    class _WfBase(DeclarativeBase):
        pass

    class _WorkflowMappingRow(_WfBase):
        __tablename__ = "nexus_workflow_mappings"

        issue_num: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64),
            primary_key=True,
        )
        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128))
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=UTC),
            onupdate=lambda: datetime.now(tz=UTC),
        )

    class _ApprovalStateRow(_WfBase):
        __tablename__ = "nexus_approval_state"

        issue_num: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64),
            primary_key=True,
        )
        step_num: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer)
        step_name: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256))
        approvers: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON list
        approval_timeout: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer)
        requested_at: sa.orm.Mapped[float] = sa.orm.mapped_column(sa.Float)


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PostgresWorkflowStateStore:
    """PostgreSQL-backed :class:`WorkflowStateStore`."""

    def __init__(
        self,
        connection_string: str,
        pool_size: int = 5,
        echo: bool = False,
    ) -> None:
        _require_sqlalchemy()

        dsn = connection_string
        if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
            dsn = dsn.replace("postgres://", "postgresql+psycopg2://", 1)

        self._engine = sa.create_engine(dsn, pool_size=pool_size, echo=echo)
        self._session_factory: sessionmaker = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )
        _WfBase.metadata.create_all(self._engine)
        logger.info(
            "PostgresWorkflowStateStore connected (%s)",
            dsn.split("@")[-1],
        )

    # ── Workflow mapping ────────────────────────────────────────────

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            now = datetime.now(tz=UTC)
            if row:
                row.workflow_id = workflow_id
                row.updated_at = now
            else:
                session.add(
                    _WorkflowMappingRow(
                        issue_num=str(issue_num),
                        workflow_id=workflow_id,
                        updated_at=now,
                    )
                )
            session.commit()
        logger.info("Mapped issue #%s -> workflow %s", issue_num, workflow_id)

    def get_workflow_id(self, issue_num: str) -> str | None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            return row.workflow_id if row else None

    def remove_mapping(self, issue_num: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            if row:
                session.delete(row)
                session.commit()
        logger.info("Removed workflow mapping for issue #%s", issue_num)

    def load_all_mappings(self) -> dict[str, str]:
        with Session(self._engine) as session:
            rows = session.query(_WorkflowMappingRow).all()
            return {r.issue_num: r.workflow_id for r in rows}

    # ── Approval gate ───────────────────────────────────────────────

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if row:
                row.step_num = step_num
                row.step_name = step_name
                row.approvers = json.dumps(approvers)
                row.approval_timeout = approval_timeout
                row.requested_at = time.time()
            else:
                session.add(
                    _ApprovalStateRow(
                        issue_num=str(issue_num),
                        step_num=step_num,
                        step_name=step_name,
                        approvers=json.dumps(approvers),
                        approval_timeout=approval_timeout,
                        requested_at=time.time(),
                    )
                )
            session.commit()
        logger.info(
            "Set pending approval for issue #%s step %d (%s)",
            issue_num,
            step_num,
            step_name,
        )

    def clear_pending_approval(self, issue_num: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if row:
                session.delete(row)
                session.commit()
        logger.info("Cleared pending approval for issue #%s", issue_num)

    def get_pending_approval(self, issue_num: str) -> dict | None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if not row:
                return None
            return {
                "step_num": row.step_num,
                "step_name": row.step_name,
                "approvers": json.loads(row.approvers),
                "approval_timeout": row.approval_timeout,
                "requested_at": row.requested_at,
            }

    def load_all_approvals(self) -> dict[str, dict]:
        with Session(self._engine) as session:
            rows = session.query(_ApprovalStateRow).all()
            return {
                r.issue_num: {
                    "step_num": r.step_num,
                    "step_name": r.step_name,
                    "approvers": json.loads(r.approvers),
                    "approval_timeout": r.approval_timeout,
                    "requested_at": r.requested_at,
                }
                for r in rows
            }

    def close(self) -> None:
        """Dispose underlying SQLAlchemy engine resources."""
        try:
            self._engine.dispose()
        except Exception as exc:
            logger.debug("Failed to dispose PostgresWorkflowStateStore engine: %s", exc)

    def __del__(self) -> None:  # pragma: no cover - defensive finalizer
        try:
            self.close()
        except Exception:
            pass
