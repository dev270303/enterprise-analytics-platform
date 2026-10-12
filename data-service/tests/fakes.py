"""Deterministic stand-in for the LLM so the AI pipeline is testable offline."""
from __future__ import annotations

import hashlib
import json
import math
import re


class FakeLLM:
    def __init__(self, sql_plans: dict[str, list[dict] | dict] | None = None):
        # question substring -> plan, or list of plans for successive attempts
        self.sql_plans = sql_plans or {}
        self.calls: list[list[dict]] = []
        self._attempts: dict[str, int] = {}

    def chat(self, messages, temperature=0.0):
        self.calls.append(messages)
        last = messages[-1]["content"]
        system = messages[0]["content"]
        if "translate business questions" in system:
            question = messages[1]["content"]
            for key, plan in self.sql_plans.items():
                if key.lower() in question.lower():
                    if isinstance(plan, list):
                        i = self._attempts.get(key, 0)
                        self._attempts[key] = i + 1
                        plan = plan[min(i, len(plan) - 1)]
                    return "```json\n" + json.dumps(plan) + "\n```"
            return json.dumps({"route": "unsupported", "sql": ""})
        if "numbered passages" in last:
            return "Refunds above ₹50,000 need Regional Sales Manager approval [1]."
        if "executive summary" in last:
            return "Revenue moved and SLAs need attention."
        return "Summary: the South region leads revenue."

    def embed(self, texts):
        return [_hash_embed(t) for t in texts]


def _hash_embed(text: str, dim: int = 768) -> list[float]:
    vec = [0.0] * dim
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
