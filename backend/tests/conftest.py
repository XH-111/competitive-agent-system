import pytest


@pytest.fixture(autouse=True)
def isolate_external_llm_keys(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SURVEY_LLM_API_KEY", raising=False)
