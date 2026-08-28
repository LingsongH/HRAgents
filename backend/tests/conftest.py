"""测试 fixtures。"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.deps import build_container
from app.llm.embeddings import EmbeddingClient
from app.pipeline.parser import DocumentParser


@pytest.fixture(scope="session")
def settings():
    return Settings(
        storage_mode="memory", vector_backend="memory", embedding_provider="hash",
        pi_agent_enabled=False, dept_id="", dept_agents_enabled=False,
        internal_api_token="test-internal-token", seed_demo_users=True,
    )


@pytest.fixture(scope="session")
def container(settings):
    """离线容器（memory 存储 + hash 向量 + 无真实 LLM）。"""
    return build_container(settings.model_copy(deep=True))


@pytest.fixture
def fresh_container(settings):
    """每个测试独立的离线容器和配置，避免测试之间修改 Settings 互相污染。"""
    return build_container(settings.model_copy(deep=True))


@pytest.fixture
def parser():
    return DocumentParser()


@pytest.fixture
def embeddings(settings):
    return EmbeddingClient(settings, relay=None)
