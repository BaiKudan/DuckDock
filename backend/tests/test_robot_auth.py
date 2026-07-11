from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, status

from app.core.deps import _auth_robot
from app.core.security import hash_password
from app.models.namespace import Namespace, NamespaceRole
from app.models.robot import RobotAccount
from app.models.user import SystemRole, User


async def _seed_robot(session, *, expires_at: datetime | None, suffix: str) -> str:
    owner = User(username=f"robot-owner-{suffix}", email=f"robot-owner-{suffix}@duckdock-ai.com", hashed_password="x")
    session.add(owner)
    await session.flush()
    namespace = Namespace(name=f"robot-ns-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()

    prefix = f"abc{suffix}"[:8]
    secret = "robot-secret"
    session.add(
        RobotAccount(
            namespace_id=namespace.id,
            name=f"ci-robot-{suffix}",
            token_hash=hash_password(secret),
            token_prefix=prefix,
            role=NamespaceRole.READONLY,
            expires_at=expires_at,
        )
    )
    await session.flush()
    return f"dkr_robot_{prefix}_{secret}"


def _auth_exc() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _assert_naive_expiry_paths(session):
    future_token = await _seed_robot(
        session,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        suffix="future",
    )
    user = await _auth_robot(future_token, session, _auth_exc())
    assert user.username == "robot:ci-robot-future"

    expired_token = await _seed_robot(
        session,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        suffix="expired",
    )
    with pytest.raises(HTTPException) as exc:
        await _auth_robot(expired_token, session, _auth_exc())
    assert exc.value.status_code == 401


async def test_auth_robot_principal_is_explicitly_user_roled(async_session):
    token = await _seed_robot(
        async_session,
        expires_at=None,
        suffix="role",
    )
    principal = await _auth_robot(token, async_session, _auth_exc())
    assert principal.system_role == SystemRole.USER
    assert principal.system_role != SystemRole.ADMIN


async def test_auth_robot_handles_naive_expiry_from_database(async_session):
    await _assert_naive_expiry_paths(async_session)


@pytest.mark.mysql
async def test_auth_robot_handles_naive_expiry_from_mysql(async_session_mysql):
    await _assert_naive_expiry_paths(async_session_mysql)
