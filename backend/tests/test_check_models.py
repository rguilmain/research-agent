import httpx
import pytest
import respx

from research_agent.check_models import check_models, run


@respx.mock
async def test_visibility_probe_keeps_optional_failures_independent():
    respx.get("https://api.openai.com/v1/models/gpt-6-luna").respond(200, json={"id": "gpt-6-luna"})
    respx.get("https://api.openai.com/v1/models/gpt-6.1-terra").respond(404, text="SECRET")
    respx.get("https://api.openai.com/v1/models/gpt-6.1-sol").mock(
        side_effect=httpx.ReadTimeout("SECRET")
    )
    async with httpx.AsyncClient() as client:
        assert await check_models(client, "SECRET") == {
            "gpt-6-luna": "visible",
            "gpt-6.1-terra": "HTTP 404",
            "gpt-6.1-sol": "unavailable",
        }


def test_command_refuses_missing_key():
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        run()


@respx.mock
def test_command_prints_only_visibility(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-SECRET")
    for model in ["gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"]:
        respx.get(f"https://api.openai.com/v1/models/{model}").respond(200, json={"id": model})
    run()
    output = capsys.readouterr().out
    assert "gpt-6-luna: visible" in output
    assert "does not verify Responses" in output
    assert "SECRET" not in output
