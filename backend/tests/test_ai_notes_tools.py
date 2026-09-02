"""Tests for AI Tools related to Notes."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import User, Note
from services.ai_action_service import _execute_global_read_tool
from services.ai_proposal_service import (
    _note_create_payload,
    _note_update_payload,
    _note_delete_payload,
    _ausfuehren_note_create,
    _ausfuehren_note_update,
    _ausfuehren_note_delete,
    _AusfuehrungsRahmen,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    user = User(
        id=1,
        username="owner",
        email="owner@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    yield session, user
    session.close()


def test_notes_ai_proposals_and_execution(db_session):
    session, user = db_session

    # 1. Build create payload
    payload, preview = _note_create_payload(
        session,
        user,
        {
            "title": "Einkaufsliste Aldi",
            "content": "- [ ] 1x Brot (~1.50 €)\n- [ ] 2x Milch (~2.20 €)",
            "category": "shopping",
            "color": "emerald",
        },
    )
    assert payload["title"] == "Einkaufsliste Aldi"
    assert payload["category"] == "shopping"

    # 2. Execute create handler with _AusfuehrungsRahmen
    rahmen_create = _AusfuehrungsRahmen(
        payload=payload,
        server_id=None,
        active_user=user,
        correlation_id="corr-test",
        expected_revision=None,
        row_id="1",
        guardian=None,
        tool_name="propose_note_create",
    )
    res_create = _ausfuehren_note_create(session, rahmen_create)
    note_data = res_create.result
    assert note_data["title"] == "Einkaufsliste Aldi"
    note_uid = note_data["note_uid"]

    # 3. Read notes via notes_read global tool
    read_res = _execute_global_read_tool(
        session, user=user, tool_name="notes_read", arguments={}
    )
    assert read_res["count"] == 1
    assert read_res["notes"][0]["title"] == "Einkaufsliste Aldi"

    # 4. Build update payload & execute
    upd_payload, _ = _note_update_payload(
        session,
        user,
        {
            "note_id": note_uid,
            "title": "Einkaufsliste Aldi Süd",
            "content": "- [ ] 1x Brot (~1.50 €)\n- [ ] 2x Milch (~2.20 €)\n- [ ] 1x Käse (~2.49 €)",
        },
    )
    rahmen_update = _AusfuehrungsRahmen(
        payload=upd_payload,
        server_id=None,
        active_user=user,
        correlation_id="corr-test",
        expected_revision=None,
        row_id="1",
        guardian=None,
        tool_name="propose_note_update",
    )
    res_update = _ausfuehren_note_update(session, rahmen_update)
    assert res_update.result["title"] == "Einkaufsliste Aldi Süd"

    # 5. Delete payload & execute
    del_payload, _ = _note_delete_payload(
        session,
        user,
        {"note_id": note_uid},
    )
    rahmen_delete = _AusfuehrungsRahmen(
        payload=del_payload,
        server_id=None,
        active_user=user,
        correlation_id="corr-test",
        expected_revision=None,
        row_id="1",
        guardian=None,
        tool_name="propose_note_delete",
    )
    res_delete = _ausfuehren_note_delete(session, rahmen_delete)
    assert res_delete.result["status"] == "deleted"

    # 6. Read again
    read_res2 = _execute_global_read_tool(
        session, user=user, tool_name="notes_read", arguments={}
    )
    assert read_res2["count"] == 0
