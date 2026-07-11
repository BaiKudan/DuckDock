import pytest
from sqlalchemy import text


@pytest.mark.mysql
async def test_mysql_lane_uses_real_database(async_session_mysql):
    result = await async_session_mysql.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
