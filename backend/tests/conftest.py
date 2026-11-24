"""Force every test onto a throwaway database.

Two of the API test modules set DB_PATH themselves; the rest relied on the
default and therefore wrote into `backend/leaseguard.db` - the database the
running app uses. That produced two real problems:

  * 106 test leases accumulated in the dev database, which is what a judge would
    have seen on screen.
  * Tests began to interfere: a lease created by one module was visible to
    another that counted leases, so the suite passed or failed depending on the
    order pytest collected files in.

Setting the variables here, before any test module imports `app`, fixes both.
`config.py` reads the environment at import time, so this has to happen first -
hence conftest rather than a fixture.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="leaseguard-tests-"))

os.environ.setdefault("DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("UPLOAD_DIR", str(_TMP / "uploads"))
os.environ.setdefault("USE_FIXTURES", "true")

# Belt and braces: even if a module already set DB_PATH, make sure it does not
# point at the developer's real database.
_real_db = (Path(__file__).resolve().parents[1] / "leaseguard.db").resolve()
if Path(os.environ["DB_PATH"]).resolve() == _real_db:
    os.environ["DB_PATH"] = str(_TMP / "test.db")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Each test starts with empty rate-limit windows.

    Without this, one test that uploads a dozen documents would leave the next
    test receiving 429s, and the failure would look like a bug in whatever the
    next test happens to check. The limits themselves are covered explicitly in
    tests/api/test_rate_limits.py.
    """
    from app.core import limits

    for limiter in limits.ALL_LIMITS:
        limiter.reset()
    yield
    for limiter in limits.ALL_LIMITS:
        limiter.reset()
