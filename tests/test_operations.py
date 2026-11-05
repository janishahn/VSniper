"""Tests for operations service, maintenance pause, search run tracking, and worker heartbeat."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import (
    MaintenanceState,
)
from vsniper.services.maintenance_service import (
    MAINTENANCE_ROW_ID,
    MaintenanceImportActive,
    begin_manual_pause,
    end_manual_pause,
    get_maintenance_state,
)


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def fake_session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return engine, factory, fake_session_scope


# --- Maintenance pause/resume tests ---


def test_manual_pause_rejected_during_import(tmp_path):
    _, factory, fake_session_scope = _setup(tmp_path)
    import vsniper.services.maintenance_service as ms
    original = ms.session_scope
    ms.session_scope = fake_session_scope
    try:
        with fake_session_scope() as session:
            session.add(MaintenanceState(id=MAINTENANCE_ROW_ID, mode="import", updated_at=datetime.now(UTC)))

        with pytest.raises(MaintenanceImportActive):
            begin_manual_pause()
    finally:
        ms.session_scope = original


def test_manual_resume_does_not_clear_import(tmp_path):
    _, factory, fake_session_scope = _setup(tmp_path)
    import vsniper.services.maintenance_service as ms
    original = ms.session_scope
    ms.session_scope = fake_session_scope
    try:
        with fake_session_scope() as session:
            session.add(MaintenanceState(id=MAINTENANCE_ROW_ID, mode="import", operation_id="imp-1", updated_at=datetime.now(UTC)))

        end_manual_pause()

        state = get_maintenance_state()
        assert state["mode"] == "import"
    finally:
        ms.session_scope = original


def test_worker_activity_skipped_during_manual_pause(tmp_path):
    _, factory, fake_session_scope = _setup(tmp_path)
    import vsniper.services.maintenance_service as ms
    original = ms.session_scope
    ms.session_scope = fake_session_scope
    try:
        with fake_session_scope() as session:
            session.add(MaintenanceState(id=MAINTENANCE_ROW_ID, mode="manual", updated_at=datetime.now(UTC)))

        with ms.worker_activity("test_phase") as active:
            assert active is False
    finally:
        ms.session_scope = original


# --- Search run tracking tests ---


# --- Worker heartbeat tests ---


# --- Operations snapshot tests ---


# --- Maintenance pause enforcement on routes ---
