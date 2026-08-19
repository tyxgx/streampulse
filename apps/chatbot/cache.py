"""
Redis-backed response cache and per-provider usage tracking for the
chatbot. Both exist to cut down on real LLM calls -- this session
repeatedly hit Groq's 100K-tokens/day cap and Gemini's ~20-requests/minute
cap, often from our own repeated/similar testing questions.

Every function here fails open: if Redis is down or unreachable, cache
checks report a miss and budget checks report "not over budget" -- the
chatbot must keep working exactly as it does with no Redis at all, same
resilience pattern apps.chatbot.services.get_bot_reply()'s try/except and
apps.chatbot.rag._call_llm()'s provider fallback chain already use.
"""
import hashlib
import json
import logging
import os

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Response cache: same/near-identical question asked again within this
# window (common during testing/demos) is answered from cache -- no
# retrieval, no LLM call, no rate-limit exposure. Short enough that it's a
# non-issue if the underlying Gold data or code changes.
CACHE_TTL_SECONDS = 900

# Conservative budgets, deliberately under the real caps observed failing
# today (Groq: 100,000 tokens/day; Gemini: ~20 requests/minute) -- these
# are observed numbers from live 429 responses, not documented guarantees,
# so some headroom is kept on purpose. Env-overridable in case the
# account's plan changes.
GROQ_DAILY_TOKEN_BUDGET = int(os.environ.get('GROQ_DAILY_TOKEN_BUDGET', 90_000))
GEMINI_MINUTE_REQUEST_BUDGET = int(os.environ.get('GEMINI_MINUTE_REQUEST_BUDGET', 15))

_USAGE_WINDOW_SECONDS = {
    'groq': 24 * 60 * 60,     # matches Groq's TPD (tokens-per-day) window
    'gemini': 60,              # matches Gemini's RPM (requests-per-minute) window
}
_USAGE_BUDGET = {
    'groq': GROQ_DAILY_TOKEN_BUDGET,
    'gemini': GEMINI_MINUTE_REQUEST_BUDGET,
}
# What record_usage()'s `amount` counts, per provider -- Groq is budgeted
# by tokens (INCRBY response usage.total_tokens each call), Gemini by
# request count (INCR by 1 each call, budget is a request-rate cap, not a
# token cap).
_USAGE_UNIT = {'groq': 'tokens', 'gemini': 'requests'}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    return _client


def _cache_key(question, history):
    # Includes history (not just question) so an empty-history question
    # (today's single-turn queries -- the biggest quota drain observed
    # this session) and a history-bearing follow-up asking the same words
    # never collide -- a follow-up's meaning depends on what came before
    # it, a fresh question doesn't.
    normalized = question.strip().lower()
    history_blob = json.dumps(history or [], sort_keys=True)
    digest = hashlib.sha256(f'{normalized}|{history_blob}'.encode()).hexdigest()
    return f'chatbot:reply:{digest}'


def get_cached_reply(question, history):
    """Returns the cached {'reply': str, 'sources': list} dict, or None on
    a cache miss (including "Redis is unreachable" -- treated the same as
    a miss, never raises)."""
    try:
        raw = _get_client().get(_cache_key(question, history))
    except redis.RedisError as e:
        logger.warning('Redis unavailable for cache read, treating as miss: %s', e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def set_cached_reply(question, history, result):
    """Best-effort -- a failed cache write should never break the actual
    reply already computed, so this swallows Redis errors rather than
    propagating them."""
    try:
        _get_client().setex(_cache_key(question, history), CACHE_TTL_SECONDS, json.dumps(result))
    except redis.RedisError as e:
        logger.warning('Redis unavailable for cache write, skipping: %s', e)


def record_usage(provider, amount):
    """Adds `amount` (tokens for groq, 1 per call for gemini -- see
    _USAGE_UNIT) to that provider's rolling usage counter. Best-effort --
    a failed usage write just means the next budget check under-counts,
    which fails open anyway (see is_over_budget())."""
    window = _USAGE_WINDOW_SECONDS.get(provider)
    if window is None:
        return
    key = f'usage:{provider}'
    try:
        client = _get_client()
        # SET ... NX ... EX atomically creates the key with a TTL only if
        # it doesn't already exist yet, so the window doesn't keep
        # sliding forward on every call. Deliberately NOT using EXPIRE's
        # NX flag for this (simpler, one command) -- that flag needs
        # Redis 7.0+, and this project's server apt-installs 6.0.16,
        # which silently failed every one of these calls until this was
        # caught in testing (SET/EXPIRE's NX/EX flags have both existed
        # since well before 6.0, just not combined on EXPIRE itself).
        client.set(key, 0, nx=True, ex=window)
        client.incrby(key, amount)
    except redis.RedisError as e:
        logger.warning('Redis unavailable for usage tracking, skipping: %s', e)


def mark_exhausted(provider):
    """Force that provider's usage counter to (at least) its budget for
    the rest of the current window. Called when a real 429 comes back
    despite our own tracking saying we had headroom (e.g. right after a
    Redis flush, or the first time a limit is hit before enough usage was
    recorded to trip the threshold ourselves) -- avoids repeatedly
    round-tripping to a provider we now know is exhausted for every
    subsequent question in the same window."""
    budget = _USAGE_BUDGET.get(provider)
    window = _USAGE_WINDOW_SECONDS.get(provider)
    if budget is None or window is None:
        return
    key = f'usage:{provider}'
    try:
        client = _get_client()
        created = client.set(key, budget, nx=True, ex=window)
        if not created:
            # Key already existed (with its own TTL from an earlier
            # record_usage()/mark_exhausted() call) -- bump the value up
            # to the budget without touching that TTL. KEEPTTL has been
            # supported since Redis 6.0, unlike EXPIRE's NX flag above.
            client.set(key, budget, xx=True, keepttl=True)
    except redis.RedisError as e:
        logger.warning('Redis unavailable to mark provider exhausted, skipping: %s', e)


def is_over_budget(provider):
    """True only when we can positively confirm usage has reached that
    provider's budget. Any uncertainty (Redis down, no usage recorded
    yet) reports False -- fail open, same as get_cached_reply()."""
    budget = _USAGE_BUDGET.get(provider)
    if budget is None:
        return False
    try:
        raw = _get_client().get(f'usage:{provider}')
    except redis.RedisError as e:
        logger.warning('Redis unavailable for budget check, assuming not over budget: %s', e)
        return False
    if raw is None:
        return False
    try:
        return int(raw) >= budget
    except (ValueError, TypeError):
        return False
