"""PostgreSQL storage backend using SQLAlchemy (sync psycopg2 driver).

Requires the ``postgres`` optional extra::

    pip install nexus-core[postgres]

All async methods delegate to synchronous SQLAlchemy sessions via
``asyncio.to_thread`` so the declared dependency (``psycopg2-binary``) is
used directly without needing an async driver.

Schema is created automatically on first use (``create_all``).
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import AuditEvent, Workflow, WorkflowState

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
            "sqlalchemy and psycopg2-binary are required for PostgreSQLStorageBackend. "
            "Install them with: pip install nexus-core[postgres]"
        )


# ---------------------------------------------------------------------------
# ORM / table definitions (only constructed when SA is available)
# ---------------------------------------------------------------------------

if _SA_AVAILABLE:

    class _Base(DeclarativeBase):
        pass

    class _WorkflowRow(_Base):
        __tablename__ = "nexus_workflows"

        id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        state: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(32))
        definition_id: sa.orm.Mapped[Optional[str]] = sa.orm.mapped_column(
            sa.String(128), nullable=True
        )
        current_step: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, default=0)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), default=lambda: datetime.now(tz=timezone.utc)
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=timezone.utc),
            onupdate=lambda: datetime.now(tz=timezone.utc),
        )

    class _AuditRow(_Base):
        __tablename__ = "nexus_audit"

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, primary_key=True, autoincrement=True)
        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), index=True)
        timestamp: sa.orm.Mapped[datetime] = sa.orm.mapped_column(sa.DateTime(timezone=True))
        event_type: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64))
        user_id: sa.orm.Mapped[Optional[str]] = sa.orm.mapped_column(sa.String(128), nullable=True)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob

    class _AgentMetaRow(_Base):
        __tablename__ = "nexus_agent_meta"

        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        agent_name: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=timezone.utc),
            onupdate=lambda: datetime.now(tz=timezone.utc),
        )


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------


class PostgreSQLStorageBackend(StorageBackend):
    """PostgreSQL-backed workflow storage using SQLAlchemy.

    .. note::
        For production deployments, it is highly recommended to use
        the ``NEXUS_STORAGE_DSN`` environment variable instead of hardcoding
        credentials in configuration files.

    Args:
        connection_string: SQLAlchemy DSN, e.g.
            ``postgresql+psycopg2://user:pass@localhost/dbname``.
        pool_size: SQLAlchemy connection pool size (default 5).
        echo: Echo SQL statements for debugging (default False).
    """

    def __init__(
        self,
        connection_string: str,
        pool_size: int = 5,
        echo: bool = False,
    ):
        _require_sqlalchemy()

        # Ensure the DSN uses the psycopg2 driver
        dsn = connection_string
        if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
            dsn = dsn.replace("postgres://", "postgresql+psycopg2://", 1)

        self._engine = sa.create_engine(dsn, pool_size=pool_size, echo=echo)
        self._session_factory: sessionmaker = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        # Create tables if they don't exist
        _Base.metadata.create_all(self._engine)
        logger.info("PostgreSQLStorageBackend connected (%s)", dsn.split("@")[-1])

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def save_workflow(self, workflow: Workflow) -> None:
        await asyncio.to_thread(self._sync_save_workflow, workflow)

    async def load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return await asyncio.to_thread(self._sync_load_workflow, workflow_id)

    async def list_workflows(
        self, state: Optional[WorkflowState] = None, limit: int = 100
    ) -> List[Workflow]:
        return await asyncio.to_thread(self._sync_list_workflows, state, limit)

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await asyncio.to_thread(self._sync_delete_workflow, workflow_id)

    async def append_audit_event(self, event: AuditEvent) -> None:
        await asyncio.to_thread(self._sync_append_audit, event)

    async def get_audit_log(
        self, workflow_id: str, since: Optional[datetime] = None
    ) -> List[AuditEvent]:
        return await asyncio.to_thread(self._sync_get_audit, workflow_id, since)

    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: Dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._sync_save_agent_meta, workflow_id, agent_name, metadata)

    async def get_agent_metadata(
        self, workflow_id: str, agent_name: str
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._sync_get_agent_meta, workflow_id, agent_name)

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return await asyncio.to_thread(self._sync_cleanup, older_than_days)

    # ------------------------------------------------------------------
    # Synchronous DB helpers
    # ------------------------------------------------------------------

    def _sync_save_workflow(self, workflow: Workflow) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow.id)
            data = self._workflow_to_dict(workflow)
            now = datetime.now(tz=timezone.utc)
            if row:
                row.state = workflow.state.value
                row.current_step = workflow.current_step
                row.data = json.dumps(data, default=str)
                row.updated_at = now
            else:
                session.add(
                    _WorkflowRow(
                        id=workflow.id,
                        state=workflow.state.value,
                        definition_id=getattr(workflow, "definition_id", None),
                        current_step=workflow.current_step,
                        data=json.dumps(data, default=str),
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()

    def _sync_load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow_id)
            if not row:
                return None
            return self._dict_to_workflow(json.loads(row.data))

    def _sync_list_workflows(
        self, state: Optional[WorkflowState], limit: int
    ) -> List[Workflow]:
        with Session(self._engine) as session:
            q = session.query(_WorkflowRow).order_by(_WorkflowRow.updated_at.desc())
            if state is not None:
                q = q.filter(_WorkflowRow.state == state.value)
            rows = q.limit(limit).all()
            workflows = []
            for row in rows:
                try:
                    workflows.append(self._dict_to_workflow(json.loads(row.data)))
                except Exception as exc:
                    logger.warning("Failed to deserialize workflow %s: %s", row.id, exc)
            return workflows

    def _sync_delete_workflow(self, workflow_id: str) -> bool:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow_id)
            if not row:
                return False
            session.delete(row)
            session.commit()
            return True

    def _sync_append_audit(self, event: AuditEvent) -> None:
        with Session(self._engine) as session:
            session.add(
                _AuditRow(
                    workflow_id=event.workflow_id,
                    timestamp=event.timestamp,
                    event_type=event.event_type,
                    user_id=event.user_id,
                    data=json.dumps(event.data, default=str),
                )
            )
            session.commit()

    def _sync_get_audit(
        self, workflow_id: str, since: Optional[datetime]
    ) -> List[AuditEvent]:
        with Session(self._engine) as session:
            q = (
                session.query(_AuditRow)
                .filter(_AuditRow.workflow_id == workflow_id)
                .order_by(_AuditRow.timestamp)
            )
            if since:
                q = q.filter(_AuditRow.timestamp >= since)
            rows = q.all()
            return [
                AuditEvent(
                    workflow_id=r.workflow_id,
                    timestamp=r.timestamp,
                    event_type=r.event_type,
                    user_id=r.user_id,
                    data=json.loads(r.data),
                )
                for r in rows
            ]

    def _sync_save_agent_meta(
        self, workflow_id: str, agent_name: str, metadata: Dict[str, Any]
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(_AgentMetaRow, (workflow_id, agent_name))
            now = datetime.now(tz=timezone.utc)
            if row:
                row.data = json.dumps(metadata, default=str)
                row.updated_at = now
            else:
                session.add(
                    _AgentMetaRow(
                        workflow_id=workflow_id,
                        agent_name=agent_name,
                        data=json.dumps(metadata, default=str),
                        updated_at=now,
                    )
                )
            session.commit()

    def _sync_get_agent_meta(
        self, workflow_id: str, agent_name: str
    ) -> Optional[Dict[str, Any]]:
        with Session(self._engine) as session:
            row = session.get(_AgentMetaRow, (workflow_id, agent_name))
            if not row:
                return None
            return json.loads(row.data)

    def _sync_cleanup(self, older_than_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)
        with Session(self._engine) as session:
            rows = (
                session.query(_WorkflowRow)
                .filter(_WorkflowRow.updated_at < cutoff)
                .all()
            )
            count = len(rows)
            for row in rows:
                session.delete(row)
            session.commit()
            return count

    # ------------------------------------------------------------------
    # Serialization helpers — shared with FileStorage
    # ------------------------------------------------------------------

    @staticmethod
    def _workflow_to_dict(workflow: Workflow) -> Dict[str, Any]:
        from nexus.adapters.storage._workflow_serde import workflow_to_dict
        return workflow_to_dict(workflow)

    @staticmethod
    def _dict_to_workflow(data: Dict[str, Any]) -> Workflow:
        from nexus.adapters.storage._workflow_serde import dict_to_workflow
        return dict_to_workflow(data)
