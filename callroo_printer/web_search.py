from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from callroo_printer.config import WebSearchConfig

LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


class WebSearchClient:
    """Minimal Brave Search client used to ground fortunes in real web results.

    Failures (missing key, network error, bad payload) degrade to an empty
    result so the caller can fall back to its normal generation path.
    """

    def __init__(self, config: WebSearchConfig) -> None:
        self.config = config
        # cache_key -> (expiry_epoch, snippets)
        self._cache: dict[str, tuple[float, tuple[str, ...]]] = {}

    def fetch_snippets(
        self, *, sign: str, date_key: str
    ) -> tuple[str, tuple[str, ...]]:
        query = self.config.query_template.replace("{sign}", sign)
        cache_key = f"{date_key}:{sign}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return query, cached[1]
        snippets = self._search(query)
        if snippets:
            self._cache[cache_key] = (now + self.config.cache_ttl_seconds, snippets)
        return query, snippets

    def _search(self, query: str) -> tuple[str, ...]:
        api_key = os.environ.get(self.config.api_key_env or "")
        if not api_key:
            LOGGER.warning(
                "Web search skipped: %s not set in environment.",
                self.config.api_key_env,
            )
            return ()

        count = max(1, self.config.count)
        url = self.config.endpoint + "?" + urllib.parse.urlencode(
            {
                "q": query,
                "count": count,
                "search_lang": "ko",
                "country": "KR",
            }
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Subscription-Token": api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            TimeoutError,
            ValueError,
        ) as exc:
            LOGGER.warning("Web search request failed for %r: %s", query, exc)
            return ()

        results = (((body or {}).get("web") or {}).get("results")) or []
        snippets: list[str] = []
        for item in results[:count]:
            if not isinstance(item, dict):
                continue
            title = _strip_tags(str(item.get("title", "")).strip())
            desc = _strip_tags(str(item.get("description", "")).strip())
            line = " — ".join(part for part in (title, desc) if part)
            if line:
                snippets.append(line)
        if not snippets:
            LOGGER.info("Web search returned no usable snippets for %r.", query)
        return tuple(snippets)


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)
