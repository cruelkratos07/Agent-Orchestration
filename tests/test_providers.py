"""
Provider selection.

Pure env -> provider resolution, so these run with no SDK installed and no key.
The point of the phase is that the model is a swappable part: nothing about the
security model, the graph, or the tool contracts is vendor-specific.
"""

import pytest

from agent_orchestration import llm


def test_no_keys_means_deterministic_mode():
    assert llm.resolve_provider({}) is None


def test_a_key_is_enough_no_config_needed():
    assert llm.resolve_provider({"GEMINI_API_KEY": "x"}) == "gemini"
    assert llm.resolve_provider({"GROQ_API_KEY": "x"}) == "groq"
    assert llm.resolve_provider({"ANTHROPIC_API_KEY": "x"}) == "anthropic"
    assert llm.resolve_provider({"OPENAI_API_KEY": "x"}) == "openai"


def test_google_api_key_is_accepted_as_well_as_gemini_api_key():
    """Google AI Studio hands out one and docs mention the other."""
    assert llm.resolve_provider({"GOOGLE_API_KEY": "x"}) == "gemini"


def test_auto_detect_follows_a_documented_order():
    both = {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "ANTHROPIC_API_KEY": "z"}
    assert llm.resolve_provider(both) == llm.PROVIDER_ORDER[0] == "gemini"


def test_explicit_provider_beats_auto_detect():
    assert (
        llm.resolve_provider({"LLM_PROVIDER": "groq", "GEMINI_API_KEY": "y"}) == "groq"
    )


def test_provider_can_be_forced_off_even_with_a_key_present():
    """Needed to demo the deterministic path on a machine that has keys set."""
    for value in ("none", "off", "test", "NONE"):
        assert (
            llm.resolve_provider({"LLM_PROVIDER": value, "GEMINI_API_KEY": "y"}) is None
        )


def test_unknown_provider_fails_loudly_and_lists_the_options():
    with pytest.raises(ValueError) as caught:
        llm.resolve_provider({"LLM_PROVIDER": "llama"})
    message = str(caught.value)
    assert "llama" in message
    for provider in llm.PROVIDERS:
        assert provider in message


def test_every_provider_has_a_default_model_and_a_signup_link():
    for name, spec in llm.PROVIDERS.items():
        assert spec["default_model"], name
        assert spec["keys"], name
        assert spec["package"].startswith("langchain-"), name
        assert spec["signup"].startswith("https://"), name


def test_model_name_defaults_per_provider_and_is_overridable():
    assert llm.model_name_for("gemini", {}) == llm.PROVIDERS["gemini"]["default_model"]
    assert llm.model_name_for("gemini", {"LLM_MODEL": "gemini-x"}) == "gemini-x"
    assert llm.model_name_for("groq", {"LLM_MODEL": "gemini-x"}) == "gemini-x"


def test_describe_is_readable_in_both_modes():
    assert "deterministic" in llm.describe()  # no keys in the test env


def test_configured_but_unusable_does_not_claim_live_mode(monkeypatch):
    """
    A key set without its SDK is CONFIGURED but not USABLE. Reporting LIVE MODE
    there sends someone hunting for a bug in the wrong place, so describe() and
    live() must both tell the truth.
    """
    # Simulate "a provider was configured": both PROVIDER and TEST_MODE are
    # resolved at import, so both have to be patched to model that state.
    monkeypatch.setattr(llm, "PROVIDER", "gemini")
    monkeypatch.setattr(llm, "TEST_MODE", False)
    monkeypatch.setattr(llm, "MODEL_NAME", "gemini-x")
    monkeypatch.setattr(llm, "_model", None)
    monkeypatch.setattr(llm, "_warned", True)  # silence the one-time warning
    monkeypatch.setattr(
        llm, "_build_model", lambda p: (_ for _ in ()).throw(ImportError())
    )

    # TEST_MODE says "configured"; live() says "usable". They differ here, and
    # that gap is exactly what the banner used to get wrong.
    assert llm.TEST_MODE is False
    assert llm.live() is False
    assert "UNAVAILABLE" in llm.describe()


def test_missing_sdk_degrades_instead_of_crashing(monkeypatch, capsys):
    """
    A key set without its package installed is a misconfiguration. It must not
    be worse than having no key: warn once, then run deterministically.
    """
    monkeypatch.setattr(llm, "PROVIDER", "gemini")
    monkeypatch.setattr(llm, "_model", None)
    monkeypatch.setattr(llm, "_warned", False)
    monkeypatch.setattr(
        llm, "_build_model", lambda provider: (_ for _ in ()).throw(ImportError())
    )

    assert llm.active_model() is None
    warning = capsys.readouterr().err
    assert "langchain-google-genai" in warning and "pip install" in warning

    assert llm.active_model() is None  # still None, and not warned twice
    assert capsys.readouterr().err == ""
