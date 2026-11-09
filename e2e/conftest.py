import pytest
from harness import agent_env


@pytest.fixture(scope="session")
def env():
    return agent_env()
