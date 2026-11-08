from __future__ import annotations

import logging
from functools import lru_cache

from ..config import get_settings

log = logging.getLogger("loom.guardrails")


class BedrockGuardrail:
    """Bedrock Guardrails check for PII and prompt attacks, used in addition to the secret scanner."""

    def __init__(self, guardrail_id: str, version: str, region: str):
        import boto3

        self.guardrail_id = guardrail_id
        self.version = version
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def check(self, text: str, *, source: str = "INPUT") -> str | None:
        """Return why the guardrail intervened, or ``None`` if the text passes.

        Deliveries are checked as INPUT because they become another agent's input (a skill is instructions), which
        is where Bedrock looks for prompt attacks. If Bedrock can't be reached the delivery is not blocked: both
        secret scanners have already run on it.
        """
        try:
            response = self.client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.version,
                source=source,
                content=[{"text": {"text": text[:20_000]}}],
            )
        except Exception:
            log.warning("Bedrock Guardrails unavailable; relying on the secret scanners", exc_info=True)
            return None
        if response.get("action") != "GUARDRAIL_INTERVENED":
            return None
        reasons: list[str] = []
        for assessment in response.get("assessments", []):
            for entity in assessment.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
                reasons.append(f"PII: {entity.get('type')}")
            for item in assessment.get("contentPolicy", {}).get("filters", []):
                kind = item.get("type")
                reasons.append("a prompt attack" if kind == "PROMPT_ATTACK" else f"content filter: {kind}")
        return "Blocked by Bedrock Guardrails" + (f" ({', '.join(sorted(set(reasons)))})" if reasons else "")


@lru_cache
def get_guardrail() -> BedrockGuardrail | None:
    settings = get_settings()
    if not settings.guardrail_id:
        return None
    return BedrockGuardrail(settings.guardrail_id, settings.guardrail_version, settings.aws_region)
