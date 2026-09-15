import pytest
from models import ChatGroupMember

from services.social_service import SocialService
from services.webrtc_rendezvous_service import (
    BlindRendezvousManager,
    GroupCallRoomRegistry,
    RoomFullError,
)
from routers.social import create_group_call_room
from services.sync_event_service import SyncEventService


@pytest.fixture(autouse=True)
def reset_rooms():
    BlindRendezvousManager.clear_all_for_testing()
    yield
    BlindRendezvousManager.clear_all_for_testing()


@pytest.mark.asyncio
async def test_group_room_capacity_and_broadcast_relay():
    token, max_peers = GroupCallRoomRegistry.create(group_id=42)
    peers = [await BlindRendezvousManager.join(token, f"peer-{i}", max_peers=max_peers) for i in range(16)]

    with pytest.raises(RoomFullError):
        await BlindRendezvousManager.join(token, "peer-16", max_peers=max_peers)

    # Every existing peer sees joins after the first participant.
    assert (await peers[0][3].get())["peer_count"] == 2
    for _ in range(14):
        await peers[0][3].get()
    for peer in peers[1:-1]:
        while not peer[3].empty():
            await peer[3].get()

    assert await BlindRendezvousManager.relay(token, peers[0][0], "opaque-signal")
    for peer in peers[1:]:
        assert (await peer[3].get())["data"] == "opaque-signal"


def test_group_call_permissions(db, owner_user, regular_user):
    group = SocialService.create_group(db, owner_user, "Calls")
    SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)

    assert SocialService.has_group_permission(db, group.id, owner_user.id, "start_group_calls")
    assert not SocialService.has_group_permission(db, group.id, regular_user.id, "start_group_calls")
    assert not SocialService.has_group_permission(db, group.id, regular_user.id, "join_group_calls")

    SocialService.update_member_role_permissions(
        db, group.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    assert SocialService.has_group_permission(db, group.id, regular_user.id, "join_group_calls")


def test_group_room_creation_broadcasts_ephemeral_token(db, owner_user, regular_user, monkeypatch):
    group = SocialService.create_group(db, owner_user, "Broadcast calls")
    SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)
    SocialService.update_member_role_permissions(
        db, group.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    events = []
    monkeypatch.setattr(
        SyncEventService,
        "publish",
        lambda payload, **kwargs: events.append((payload, kwargs.get("user_id"))) or 1,
    )

    result = create_group_call_room(group.id, db, owner_user)

    assert result["room_token"].startswith("grp_")
    assert {user_id for _, user_id in events} == {owner_user.id, regular_user.id}
    assert all(payload["type"] == "group_call_started" for payload, _ in events)
    assert all(payload["room_token"] == result["room_token"] for payload, _ in events)
    assert db.query(ChatGroupMember).filter(ChatGroupMember.group_id == group.id).count() == 2
