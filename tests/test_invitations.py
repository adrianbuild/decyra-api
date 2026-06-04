"""Task 2.3 — invitations & roles: onboard_user join path, role
enforcement, email binding, and org-scoped RLS."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests._helpers import add_member, seed_invitation, seed_org_with_owner

OWNER = "11111111-1111-1111-1111-111111111111"
NEWBIE = "22222222-2222-2222-2222-222222222222"
ADMIN = "33333333-3333-3333-3333-333333333333"
USER = "44444444-4444-4444-4444-444444444444"
OWNER_B = "55555555-5555-5555-5555-555555555555"
NOBODY = "99999999-9999-9999-9999-999999999999"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count_orgs(db: Connection) -> int:
    return db.execute(text("SELECT count(*) FROM organizations")).scalar_one()


# -- onboard_user paths --------------------------------------------------


@pytest.mark.asyncio
async def test_invited_user_joins_org_as_user(
    client, db: Connection, make_token
) -> None:
    org_id, ws_id = seed_org_with_owner(db, OWNER, "owner@firma.de")
    seed_invitation(db, org_id, "newbie@firma.de", invited_by=OWNER, role="user")

    token = make_token(sub=NEWBIE, email="newbie@firma.de")
    r = await client.post("/onboarding", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["created"] is False  # joined, did not found

    assert _count_orgs(db) == 1  # NO new org
    row = db.execute(
        text(
            "SELECT workspace_id, role FROM workspace_members "
            "WHERE user_id = :u"
        ),
        {"u": NEWBIE},
    ).one()
    assert str(row.workspace_id) == ws_id
    assert row.role == "user"
    status = db.execute(
        text("SELECT status FROM invitations WHERE email = 'newbie@firma.de'")
    ).scalar_one()
    assert status == "accepted"


@pytest.mark.asyncio
async def test_invited_user_joins_as_admin(
    client, db: Connection, make_token
) -> None:
    org_id, _ = seed_org_with_owner(db, OWNER, "owner@firma.de")
    seed_invitation(db, org_id, "boss@firma.de", invited_by=OWNER, role="admin")

    token = make_token(sub=NEWBIE, email="boss@firma.de")
    r = await client.post("/onboarding", headers=_auth(token))
    assert r.status_code == 200
    role = db.execute(
        text("SELECT role FROM workspace_members WHERE user_id = :u"),
        {"u": NEWBIE},
    ).scalar_one()
    assert role == "admin"


@pytest.mark.asyncio
async def test_email_mismatch_falls_to_founder(
    client, db: Connection, make_token
) -> None:
    org_id, _ = seed_org_with_owner(db, OWNER, "owner@firma.de")
    seed_invitation(db, org_id, "a@firma.de", invited_by=OWNER)

    # Login email differs from the invited email -> no join, founds own org.
    token = make_token(sub=NEWBIE, email="b@firma.de")
    r = await client.post("/onboarding", headers=_auth(token))
    assert r.json()["created"] is True
    assert _count_orgs(db) == 2
    status = db.execute(
        text("SELECT status FROM invitations WHERE email = 'a@firma.de'")
    ).scalar_one()
    assert status == "pending"  # untouched


@pytest.mark.asyncio
async def test_expired_invitation_falls_to_founder(
    client, db: Connection, make_token
) -> None:
    org_id, _ = seed_org_with_owner(db, OWNER, "owner@firma.de")
    seed_invitation(
        db, org_id, "late@firma.de", invited_by=OWNER, expires_days=-1
    )
    token = make_token(sub=NEWBIE, email="late@firma.de")
    r = await client.post("/onboarding", headers=_auth(token))
    assert r.json()["created"] is True  # founder, not joined
    assert _count_orgs(db) == 2


@pytest.mark.asyncio
async def test_revoked_invitation_falls_to_founder(
    client, db: Connection, make_token
) -> None:
    org_id, _ = seed_org_with_owner(db, OWNER, "owner@firma.de")
    seed_invitation(
        db, org_id, "gone@firma.de", invited_by=OWNER, status="revoked"
    )
    token = make_token(sub=NEWBIE, email="gone@firma.de")
    r = await client.post("/onboarding", headers=_auth(token))
    assert r.json()["created"] is True
    assert _count_orgs(db) == 2


# -- invitation endpoints + role enforcement -----------------------------


@pytest.mark.asyncio
async def test_create_invitation_as_owner(
    client, db: Connection, make_token, mail_outbox
) -> None:
    org_id, _ = seed_org_with_owner(db, OWNER, "owner@firma.de")
    token = make_token(sub=OWNER, email="owner@firma.de")
    r = await client.post(
        "/invitations",
        headers=_auth(token),
        json={"email": "x@firma.de", "role": "user"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "x@firma.de"
    assert body["role"] == "user"
    assert body["mail_sent"] is True
    assert body["invite_link"].endswith(body["token"])
    assert mail_outbox == [
        {"to": "x@firma.de", "token": body["token"], "role": "user"}
    ]
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM invitations WHERE organization_id = :o"
            ),
            {"o": org_id},
        ).scalar_one()
        == 1
    )


@pytest.mark.asyncio
async def test_create_invitation_as_admin(
    client, db: Connection, make_token
) -> None:
    _, ws_id = seed_org_with_owner(db, OWNER, "owner@firma.de")
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, :e)"),
        {"i": ADMIN, "e": "admin@firma.de"},
    )
    add_member(db, ws_id, ADMIN, "admin")
    token = make_token(sub=ADMIN, email="admin@firma.de")
    r = await client.post(
        "/invitations",
        headers=_auth(token),
        json={"email": "y@firma.de", "role": "user"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_create_invitation_as_user_forbidden(
    client, db: Connection, make_token
) -> None:
    _, ws_id = seed_org_with_owner(db, OWNER, "owner@firma.de")
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, :e)"),
        {"i": USER, "e": "user@firma.de"},
    )
    add_member(db, ws_id, USER, "user")
    token = make_token(sub=USER, email="user@firma.de")
    r = await client.post(
        "/invitations",
        headers=_auth(token),
        json={"email": "z@firma.de", "role": "user"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_no_membership_forbidden(
    client, make_token
) -> None:
    token = make_token(sub=NOBODY, email="nobody@firma.de")
    r = await client.post(
        "/invitations",
        headers=_auth(token),
        json={"email": "z@firma.de", "role": "user"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_owner_role_rejected(
    client, db: Connection, make_token
) -> None:
    seed_org_with_owner(db, OWNER, "owner@firma.de")
    token = make_token(sub=OWNER, email="owner@firma.de")
    r = await client.post(
        "/invitations",
        headers=_auth(token),
        json={"email": "z@firma.de", "role": "owner"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_invitations_scoped_to_own_org(
    client, db: Connection, make_token
) -> None:
    org_a, _ = seed_org_with_owner(db, OWNER, "ownerA@firma.de")
    seed_invitation(db, org_a, "a-invitee@firma.de", invited_by=OWNER)
    org_b, _ = seed_org_with_owner(db, OWNER_B, "ownerB@firma.de")
    seed_invitation(db, org_b, "b-invitee@firma.de", invited_by=OWNER_B)

    token = make_token(sub=OWNER, email="ownerA@firma.de")
    r = await client.get("/invitations", headers=_auth(token))
    assert r.status_code == 200
    assert [i["email"] for i in r.json()] == ["a-invitee@firma.de"]


@pytest.mark.asyncio
async def test_revoke_invitation(
    client, db: Connection, make_token
) -> None:
    org_a, _ = seed_org_with_owner(db, OWNER, "ownerA@firma.de")
    tok = seed_invitation(db, org_a, "revoke-me@firma.de", invited_by=OWNER)
    token = make_token(sub=OWNER, email="ownerA@firma.de")
    r = await client.post(f"/invitations/{tok}/revoke", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    status = db.execute(
        text("SELECT status FROM invitations WHERE token = :t"), {"t": tok}
    ).scalar_one()
    assert status == "revoked"


@pytest.mark.asyncio
async def test_revoke_other_org_invitation_404(
    client, db: Connection, make_token
) -> None:
    seed_org_with_owner(db, OWNER, "ownerA@firma.de")
    org_b, _ = seed_org_with_owner(db, OWNER_B, "ownerB@firma.de")
    tok_b = seed_invitation(db, org_b, "b-invitee@firma.de", invited_by=OWNER_B)

    token = make_token(sub=OWNER, email="ownerA@firma.de")
    r = await client.post(f"/invitations/{tok_b}/revoke", headers=_auth(token))
    assert r.status_code == 404


def test_invitations_rls_cross_org_as_decyra_app(db: Connection) -> None:
    """Data-layer proof: under decyra_app with org A context, org B's
    invitations are invisible (RLS), not just filtered in app code."""
    org_a, _ = seed_org_with_owner(db, OWNER, "ownerA@firma.de")
    org_b, _ = seed_org_with_owner(db, OWNER_B, "ownerB@firma.de")
    seed_invitation(db, org_a, "a@firma.de", invited_by=OWNER)
    seed_invitation(db, org_b, "b@firma.de", invited_by=OWNER_B)

    db.execute(text("SET LOCAL ROLE decyra_app"))
    who = db.execute(
        text("SELECT current_user, current_setting('is_superuser')")
    ).one()
    assert who[0] == "decyra_app" and who[1] == "off"

    db.execute(
        text("SELECT set_config('app.current_organization_id', :o, true)"),
        {"o": org_a},
    )
    emails = (
        db.execute(text("SELECT email FROM invitations ORDER BY email"))
        .scalars()
        .all()
    )
    assert emails == ["a@firma.de"]  # B invisible
