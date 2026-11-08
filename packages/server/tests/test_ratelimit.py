from types import SimpleNamespace

import pytest

from loom_server.ratelimit import Limit, Limiter, RateLimited, client_address


def _request(headers: dict, host: str = "10.0.0.5"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host=host))


def test_a_flood_is_stopped_and_the_window_slides():
    limiter, limit = Limiter(), Limit(3, 60, "join attempts")
    for second in range(3):
        limiter.check(("join", "1.2.3.4"), limit, now=second)
    with pytest.raises(RateLimited) as stopped:
        limiter.check(("join", "1.2.3.4"), limit, now=10)
    assert "Too many join attempts" in str(stopped.value) and stopped.value.retry_after == 51
    limiter.check(("join", "5.6.7.8"), limit, now=10)  # someone else is not affected
    limiter.check(("join", "1.2.3.4"), limit, now=61)  # the oldest attempt has aged out


def test_the_visitor_address_cant_be_faked_through_cloudfront():
    through_cloudfront = {"X-Origin-Verify": "x", "X-Forwarded-For": "6.6.6.6, 203.0.113.9, 130.176.1.1"}
    assert client_address(_request(through_cloudfront)) == "203.0.113.9"
    assert client_address(_request({"X-Forwarded-For": "6.6.6.6"})) == "10.0.0.5"  # not via CloudFront: ignored


def test_the_gateway_answers_429(api_less_client):
    statuses = [api_less_client.post("/api/dashboard/session", json={"code": "dsh_nope"}).status_code
                for _ in range(61)]
    assert statuses[:60] == [403] * 60 and statuses[60] == 429


@pytest.fixture
def api_less_client(monkeypatch):
    from fastapi.testclient import TestClient

    from loom_server import api
    from loom_server.ratelimit import limiter

    limiter.reset()
    monkeypatch.setattr(api, "open_pool", lambda: None)
    monkeypatch.setattr(api, "close_pool", lambda: None)
    with TestClient(api.app) as client:
        yield client
    limiter.reset()


def test_guardrail_findings_are_explained_and_an_outage_never_blocks(monkeypatch):
    from loom_server.providers.guardrails import BedrockGuardrail

    guardrail = BedrockGuardrail.__new__(BedrockGuardrail)
    guardrail.guardrail_id, guardrail.version = "g", "1"
    calls = []

    def intervened(**kwargs):
        calls.append(kwargs["source"])
        return {"action": "GUARDRAIL_INTERVENED", "assessments": [{
            "sensitiveInformationPolicy": {"piiEntities": [{"type": "CREDIT_DEBIT_CARD_NUMBER"}]},
            "contentPolicy": {"filters": [{"type": "PROMPT_ATTACK"}]}}]}

    guardrail.client = SimpleNamespace(apply_guardrail=intervened)
    assert guardrail.check("x") == "Blocked by Bedrock Guardrails (PII: CREDIT_DEBIT_CARD_NUMBER, a prompt attack)"
    assert calls == ["INPUT"]

    def down(**kwargs):
        raise ConnectionError("bedrock unreachable")

    guardrail.client = SimpleNamespace(apply_guardrail=down)
    assert guardrail.check("x") is None
