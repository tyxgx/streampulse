"""LLM access: Groq first, Gemini (OpenAI-compatible endpoint) as fallback."""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

log = logging.getLogger("chatbot.llm")

GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
THINK = re.compile(r"<think>.*?</think>", re.S)


def _retry_after(e) -> float:
    try:
        return float(e.response.headers.get("retry-after", 99))
    except Exception:
        return 99.0


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Reply:
    content: str
    tool_calls: list = field(default_factory=list)  # [{id, name, arguments(str)}]
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


class LLM:
    def __init__(self, groq_key=None, gemini_key=None, groq_model=None, gemini_model=None, timeout=25):
        groq_key = groq_key or os.environ.get("GROQ_API_KEY")
        gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")
        self.providers = []
        if groq_key:
            client = OpenAI(api_key=groq_key, base_url=GROQ_URL, timeout=timeout, max_retries=0)
            # rate limits are per model on Groq, so a chain of models is a cheap fallback
            chain = ["openai/gpt-oss-120b", groq_model or os.environ.get("GROQ_MODEL"), "openai/gpt-oss-20b"]
            for m in dict.fromkeys(x for x in chain if x):
                self.providers.append(("groq", client, m))
        if gemini_key:
            gc = OpenAI(api_key=gemini_key, base_url=GEMINI_URL, timeout=timeout, max_retries=0)
            for m in dict.fromkeys(x for x in [gemini_model or os.environ.get("GEMINI_MODEL"), "gemini-3.1-flash-lite", "gemini-3-flash-preview"] if x):
                self.providers.append(("gemini", gc, m))
        if not self.providers:
            raise LLMUnavailable("No LLM API key configured")

    def chat(self, messages, tools=None, temperature=0.0, max_tokens=700) -> Reply:
        try:
            return self._chain(messages, tools, temperature, max_tokens)
        except LLMUnavailable as e:
            # everything was rate limited: wait out a short limit once and sweep the chain again
            wait = getattr(e, "wait", 99)
            if wait > 8:
                raise
            time.sleep(wait + 0.3)
            return self._chain(messages, tools, temperature, max_tokens)

    @staticmethod
    def _for(provider, messages):
        """Provider-specific copy of the conversation. Gemini 3 requires a thought signature on every function call it sees
        (its own are passed back; calls made by another provider get Google's documented skip marker). Other providers must not
        receive that field."""
        out = []
        for m in messages:
            tcs = m.get("tool_calls") if isinstance(m, dict) else None
            if not tcs:
                out.append(m)
                continue
            fixed = []
            for tc in tcs:
                tc = dict(tc)
                if provider == "gemini":
                    tc.setdefault("extra_content", {"google": {"thought_signature": "skip_thought_signature_validator"}})
                else:
                    tc.pop("extra_content", None)
                fixed.append(tc)
            out.append(dict(m, tool_calls=fixed))
        return out

    def _chain(self, messages, tools, temperature, max_tokens) -> Reply:
        last, waits = None, []
        for name, client, model in self.providers:
            for attempt in range(2):
                try:
                    kw = dict(model=model, messages=self._for(name, messages), temperature=temperature, max_tokens=max_tokens)
                    if tools:
                        kw["tools"] = [{"type": "function", "function": t} for t in tools]
                        kw["tool_choice"] = "auto"
                    r = client.chat.completions.create(**kw)
                    m = r.choices[0].message
                    calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}",
                              "extra_content": getattr(c, "extra_content", None) or (getattr(c, "model_extra", None) or {}).get("extra_content")}
                             for c in (m.tool_calls or [])]
                    u = r.usage
                    return Reply(THINK.sub("", m.content or "").strip(), calls, f"{name}:{model}",
                                 getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
                except RateLimitError as e:
                    last = e
                    wait = _retry_after(e)
                    waits.append(wait)
                    log.warning("%s/%s rate limited (retry-after %.1fs)", name, model, wait)
                    if attempt == 0 and wait <= 2.0:
                        time.sleep(wait)
                        continue
                    break  # next model/provider
                except (APIConnectionError, APIStatusError) as e:
                    last = e
                    body = str(getattr(e, "message", e))[:300].replace("\n", " ")
                    log.warning("%s/%s error %s (attempt %d): %s", name, model, getattr(e, "status_code", type(e).__name__), attempt + 1, body)
                    if isinstance(e, APIStatusError) and e.status_code < 500 and e.status_code != 429:
                        break
                    time.sleep(0.6 * (attempt + 1))
        err = LLMUnavailable(f"All LLM providers failed: {type(last).__name__}")
        err.wait = min(waits) if waits and isinstance(last, RateLimitError) else 99
        raise err
