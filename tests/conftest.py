import pytest


@pytest.fixture(autouse=True)
def isolated_agent_flag(monkeypatch):
    # LocalBot loads the real .env, and load_dotenv never overrides a variable
    # that is already set: pin the agent choice so tests never depend on this Mac.
    monkeypatch.setenv('AGENT_V2', '0')
