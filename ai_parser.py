"""
ai_parser.py — Claude Haiku parses forwarded messages into structured quest data.
Falls back gracefully if ANTHROPIC_API_KEY is not set.
"""
import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a task extraction assistant for a personal productivity bot.
Extract the actionable task from the given message and return ONLY a JSON object.
No preamble, no markdown fences, just raw JSON.

JSON structure:
{
  "task": "concise action item (max 80 chars, imperative verb)",
  "priority": "low|medium|high|critical",
  "tag": "#accurova|#dev|#tutoring|#personal|#busking|#general",
  "due": "natural language date string or null"
}

Tag rules:
- #accurova: photography, studio, clients, shoots, invoices, retouching, Canon, Nikon
- #dev: code, bots, deploy, bugs, GitHub, scripts, apps, APIs, Zeabur
- #tutoring: students (Angela, Denzel, Pakorn, Jessica, Theethus, Rin, Poon), lessons, math, worksheets
- #busking: FattKew, NAC, busking, OneBoyBand
- #personal: cosplay, Miku, figures, errands, personal admin

Priority rules:
- critical: urgent/blocking/ASAP/deadline today
- high: important, due soon, client-facing
- medium: normal work tasks
- low: nice-to-have, someday"""


async def parse_forwarded_message(text: str) -> dict:
    """Use Claude Haiku to extract task intent from a forwarded message."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"task": text[:200], "priority": "medium", "tag": None, "due": None}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 200,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": f"Message:\n{text[:800]}"}],
                },
            )
            data = resp.json()
            raw  = data["content"][0]["text"].strip()
            # Strip accidental markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw.strip())
            # Sanitise
            parsed["task"]     = str(parsed.get("task", text[:200]))[:300]
            parsed["priority"] = parsed.get("priority", "medium")
            if parsed["priority"] not in ("low", "medium", "high", "critical"):
                parsed["priority"] = "medium"
            parsed["tag"] = parsed.get("tag") or None
            parsed["due"] = parsed.get("due") or None
            return parsed
    except Exception as e:
        logger.warning(f"[AI Parser] Failed: {e} — falling back to raw text")
        return {"task": text[:200], "priority": "medium", "tag": None, "due": None}
