"""The pre-demo AI check: one tiny request per configured service, never patient data."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.providers.llm.gemini import GeminiProvider
from app.cli import _ai_check
from app.config import Settings


class _GeminiNetwork:
    """Gemini's network client: answers, or fails like an outage."""

    def __init__(self, *, down: bool) -> None:
        self.down = down
        self.prompts: list[Any] = []
        self.models = self

    async def generate_content(self, *, model: str, contents: Any, config: object) -> object:
        del model, config
        self.prompts.append(contents)
        if self.down:
            raise ConnectionError("synthetic outage")
        return SimpleNamespace(parsed={"ready": True}, text=None, usage_metadata=None)

    async def aclose(self) -> None:
        return None


def _use(monkeypatch: pytest.MonkeyPatch, network: _GeminiNetwork | None) -> None:
    keyed = network is not None
    settings = Settings(
        _env_file=None,
        gemini_api_key="synthetic-test-key" if keyed else "",
        sarvam_api_key="",
        tts_provider="browser",
        stt_provider="browser",
    )
    monkeypatch.setattr("app.cli.get_settings", lambda: settings)
    if keyed:
        monkeypatch.setattr(
            "app.ai.providers.llm.factory.build_llm_provider",
            lambda _settings: GeminiProvider(
                api_key="synthetic-test-key",
                model="gemini-test",
                fallback_model="gemini-test-fallback",
                client=network,
            ),
        )


async def test_without_keys_the_demo_runs_on_rules(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, None)
    assert await _ai_check() == 0
    printed = capsys.readouterr().out
    assert "AI: off (mock)" in printed and "browser's own voices" in printed


async def test_a_live_ai_answers_with_no_patient_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    network = _GeminiNetwork(down=False)
    _use(monkeypatch, network)
    assert await _ai_check() == 0
    assert "AI: Gemini (gemini-test) answered" in capsys.readouterr().out
    assert network.prompts == ["Reply with ready set to true."]


async def test_an_outage_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _GeminiNetwork(down=True))
    assert await _ai_check() == 1
    assert "Gemini did not answer" in capsys.readouterr().out
