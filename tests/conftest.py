import os
import sys
import tempfile
from pathlib import Path

# Use a throwaway database for tests. This must run before scholarpilot is imported.
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.as_posix()}"
os.environ["GROQ_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""
# Pin model settings so a developer's .env doesn't change test results
os.environ["LLM_REASONING_EFFORT"] = "low"
os.environ["LLM_MAX_OUTPUT_TOKENS"] = "2048"
os.environ["LLM_TPM_LIMIT"] = "8000"
os.environ["CONTEXT_KEEP_TURNS"] = "3"
os.environ["ADMIN_EMAILS"] = "admin@example.com"
os.environ["MONITOR_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from scholarpilot.db import init_db, session_scope  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    yield


@pytest.fixture
def db():
    with session_scope() as s:
        yield s
