"""
LLM RESPONSE CACHE — Redis when REDIS_URL is set, in-process otherwise.

WHY A CACHE EARNS ITS PLACE HERE
--------------------------------
Not for latency. Because free-tier rate limits are the real constraint since
Phase 9, and this system re-asks the model the same questions constantly:

  * the supervisor runs once per HOP, so a two-specialist turn is three routing
    calls - and the second and third see the same user text;
  * risk_summary sits above interrupt(), so Phase 6's ordering rule makes it
    re-run on every resume;
  * demos and tests replay identical conversations.

All model calls here use temperature=0, so identical input means identical
output and caching is sound rather than a correctness risk.

WHAT IS NOT CACHED
------------------
Nothing that reads the database. Caching order or refund state would be both a
staleness bug and a security surface - a cached answer could outlive the
customer_id scoping that produced it. Only model responses are cached, keyed by
the full prompt plus model configuration.

WHY NOT langchain-redis
-----------------------
Its RedisCache requires Redis Stack: it issues JSON.GET and fails on a vanilla
server with `unknown command 'JSON.GET'`. Most free managed Redis tiers, the
redis:7-alpine image and a plain apt install are all vanilla. Forty lines of
BaseCache runs everywhere instead.
"""

import hashlib
import os
import warnings

from langchain_core.caches import BaseCache, InMemoryCache
from langchain_core.globals import set_llm_cache
from langchain_core.load import dumps, loads
from langchain_core.outputs import ChatGeneration

REDIS_URL = os.environ.get("REDIS_URL") or None
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))
KEY_PREFIX = "orderagent:llm"


class RedisResponseCache(BaseCache):
    """
    A LangChain cache on plain Redis.

    Stores the MESSAGE rather than the ChatGeneration wrapper and rebuilds the
    wrapper on read. That is what keeps `allowed_objects="messages"` viable:
    only chat message classes are ever rehydrated from Redis, so a shared or
    compromised cache cannot be used to instantiate arbitrary classes.

    Tool calls survive the round trip. That matters more than it looks: a cache
    that dropped tool_calls would silently break the tool-calling loop the
    second time any prompt was seen, and only then.
    """

    def __init__(self, url, ttl=CACHE_TTL_SECONDS, prefix=KEY_PREFIX):
        import redis

        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.ttl = ttl
        self.prefix = prefix

    def _key(self, prompt, llm_string):
        digest = hashlib.sha256(f"{prompt}\x00{llm_string}".encode()).hexdigest()
        return f"{self.prefix}:e:{digest}"

    def lookup(self, prompt, llm_string):
        raw = self.client.get(self._key(prompt, llm_string))
        if raw is None:
            self.client.incr(f"{self.prefix}:stats:misses")
            return None
        self.client.incr(f"{self.prefix}:stats:hits")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")     # loads() is still marked beta
            return [ChatGeneration(message=loads(raw, allowed_objects="messages"))]

    def update(self, prompt, llm_string, return_val):
        message = getattr(return_val[0], "message", None) if return_val else None
        if message is None:
            return                              # not a chat generation; skip
        self.client.setex(self._key(prompt, llm_string), self.ttl, dumps(message))

    def clear(self, **kwargs):
        for key in self.client.scan_iter(f"{self.prefix}:*"):
            self.client.delete(key)

    def stats(self):
        hits = int(self.client.get(f"{self.prefix}:stats:hits") or 0)
        misses = int(self.client.get(f"{self.prefix}:stats:misses") or 0)
        total = hits + misses
        return {
            "backend": "redis",
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 3) if total else None,
            "ttl_seconds": self.ttl,
            "entries": sum(1 for _ in self.client.scan_iter(f"{self.prefix}:e:*")),
        }


_cache = None


def install():
    """
    Register the cache with LangChain. Returns a description for logs.

    Falls back to an in-process cache if REDIS_URL is unset OR unreachable. A
    cache is an optimisation; being unable to reach it must never take the
    system down.
    """
    global _cache
    if REDIS_URL:
        try:
            candidate = RedisResponseCache(REDIS_URL)
            candidate.client.ping()             # fail fast, not on first query
            _cache = candidate
            set_llm_cache(_cache)
            return f"redis (ttl {CACHE_TTL_SECONDS}s)"
        except Exception as error:
            print(f"[cache] Redis unreachable ({type(error).__name__}), "
                  f"using in-process cache instead")
    _cache = InMemoryCache()
    set_llm_cache(_cache)
    return "in-process"


def stats():
    if isinstance(_cache, RedisResponseCache):
        return _cache.stats()
    return {"backend": "in-process" if _cache is not None else "none"}


def clear():
    if _cache is not None:
        _cache.clear()
