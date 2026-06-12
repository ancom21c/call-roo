from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from callroo_printer.config import WebSearchConfig

LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_OHAASA_PAGE_URL = "https://www.asahi.co.jp/ohaasa/week/horoscope/index.html"
_DAYSAAJU_PAGE_URL = "https://daysaju.com/fortune/zodiac"
_OHAASA_SIGNS = {
    "01": ("양", "양자리", "牡羊座"),
    "02": ("황소", "황소자리", "牡牛座"),
    "03": ("쌍둥이", "쌍둥이자리", "双子座"),
    "04": ("게", "게자리", "蟹座"),
    "05": ("사자", "사자자리", "獅子座"),
    "06": ("처녀", "처녀자리", "乙女座"),
    "07": ("천칭", "천칭자리", "天秤座"),
    "08": ("전갈", "전갈자리", "蠍座"),
    "09": ("사수", "사수자리", "射手座"),
    "10": ("염소", "염소자리", "山羊座"),
    "11": ("물병", "물병자리", "水瓶座"),
    "12": ("물고기", "물고기자리", "魚座"),
}
_DAYSAAJU_SIGNS = (
    ("rat", "쥐", "쥐띠", "子"),
    ("ox", "소", "소띠", "丑"),
    ("tiger", "호랑이", "호랑이띠", "寅"),
    ("rabbit", "토끼", "토끼띠", "卯"),
    ("dragon", "용", "용띠", "辰"),
    ("snake", "뱀", "뱀띠", "巳"),
    ("horse", "말", "말띠", "午"),
    ("sheep", "양", "양띠", "未"),
    ("monkey", "원숭이", "원숭이띠", "申"),
    ("rooster", "닭", "닭띠", "酉"),
    ("dog", "개", "개띠", "戌"),
    ("pig", "돼지", "돼지띠", "亥"),
)
_DAYSAAJU_SLUGS = {slug for slug, _sign_key, _sign_name, _hanja in _DAYSAAJU_SIGNS}
_DAYSAAJU_GRADES = ("대길", "중길", "소길", "길", "평", "소흉", "흉", "대흉")


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    description: str

    def snippet(self) -> str:
        return " — ".join(part for part in (self.title, self.description) if part)

    def as_tool_payload(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "description": self.description,
        }


class WebSearchClient:
    """Minimal web search client used to ground fortunes in real web results.

    Failures (missing key, network error, bad payload) degrade to an empty
    result so the caller can fall back to its normal generation path.
    """

    def __init__(self, config: WebSearchConfig) -> None:
        self.config = config
        # cache_key -> (expiry_epoch, structured results)
        self._cache: dict[str, tuple[float, tuple[WebSearchResult, ...]]] = {}
        self._ohaasa_cache: tuple[float, dict[str, WebSearchResult]] | None = None
        self._daysaju_cache: tuple[float, dict[str, WebSearchResult]] | None = None

    def fetch_snippets(
        self, *, sign: str, date_key: str
    ) -> tuple[str, tuple[str, ...]]:
        query = self.config.query_template.replace("{sign}", sign)
        cache_key = f"snippet:{date_key}:{query}"
        results = self.search(query, cache_key=cache_key)
        return query, tuple(result.snippet() for result in results if result.snippet())

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        cache_key: str | None = None,
    ) -> tuple[WebSearchResult, ...]:
        clean_query = query.strip()
        if not clean_query:
            return ()

        result_count = _clamp_count(count if count is not None else self.config.count)
        cache_key = cache_key or (
            f"{self.config.provider}:{self.config.endpoint}:"
            f"{self.config.search_lang}:{self.config.country}:"
            f"{getattr(self.config, 'search_depth', 'basic')}:"
            f"{result_count}:{clean_query}"
        )
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]

        results = self._search(clean_query, count=result_count)
        if results:
            self._cache[cache_key] = (now + self.config.cache_ttl_seconds, results)
        return results

    def _search(self, query: str, *, count: int) -> tuple[WebSearchResult, ...]:
        provider = self.config.provider.strip().lower()
        if provider == "brave":
            return self._search_brave(query, count=count)
        if provider == "tavily":
            return self._search_tavily(query, count=count)
        if provider == "ohaasa":
            return self._search_ohaasa(query, count=count)
        if provider == "daysaju":
            return self._search_daysaju(query, count=count)
        LOGGER.warning("Web search skipped: unsupported provider %r.", provider)
        return ()

    def _search_brave(self, query: str, *, count: int) -> tuple[WebSearchResult, ...]:
        api_key = self._api_key()
        if not api_key:
            LOGGER.warning(
                "Web search skipped: %s not set in environment.",
                self.config.api_key_env,
            )
            return ()

        url = self.config.endpoint + "?" + urllib.parse.urlencode(
            {
                "q": query,
                "count": count,
                "search_lang": self.config.search_lang,
                "country": self.config.country,
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
        body = self._open_json(request, query=query)
        if body is None:
            return ()

        results = (((body or {}).get("web") or {}).get("results")) or []
        parsed_results = _parse_brave_results(results, count=count)
        if not parsed_results:
            LOGGER.info("Web search returned no usable snippets for %r.", query)
        return parsed_results

    def _search_ohaasa(self, query: str, *, count: int) -> tuple[WebSearchResult, ...]:
        results_by_sign = self._fetch_ohaasa_results()
        if not results_by_sign:
            return ()
        sign_key = _extract_ohaasa_sign(query)
        if sign_key:
            result = results_by_sign.get(sign_key)
            return (result,) if result is not None else ()

        results = list(results_by_sign.values())
        results.sort(key=_ohaasa_result_rank)
        return tuple(results[:count])

    def _search_daysaju(self, query: str, *, count: int) -> tuple[WebSearchResult, ...]:
        results_by_sign = self._fetch_daysaju_results()
        if not results_by_sign:
            return ()
        sign_key = _extract_daysaju_sign(query)
        if sign_key:
            result = results_by_sign.get(sign_key)
            return (result,) if result is not None else ()

        results = list(results_by_sign.values())
        return tuple(results[:count])

    def _search_tavily(self, query: str, *, count: int) -> tuple[WebSearchResult, ...]:
        api_key = self._api_key()
        if not api_key:
            LOGGER.warning(
                "Web search skipped: %s not set in environment.",
                self.config.api_key_env,
            )
            return ()

        payload: dict[str, object] = {
            "query": query,
            "max_results": count,
            "search_depth": _normalize_tavily_search_depth(
                getattr(self.config, "search_depth", "basic")
            ),
            "include_answer": bool(getattr(self.config, "include_answer", False)),
            "include_raw_content": bool(
                getattr(self.config, "include_raw_content", False)
            ),
        }
        country = _normalize_tavily_country(self.config.country)
        if country:
            payload["country"] = country

        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        body = self._open_json(request, query=query)
        if body is None:
            return ()

        results = ((body or {}).get("results")) or []
        parsed_results = _parse_tavily_results(results, count=count)
        if not parsed_results:
            LOGGER.info("Web search returned no usable snippets for %r.", query)
        return parsed_results

    def _open_json(
        self,
        request: urllib.request.Request,
        *,
        query: str,
    ) -> dict[str, object] | None:
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
            return None
        if isinstance(body, dict):
            return body
        LOGGER.warning("Web search response root was not an object for %r.", query)
        return None

    def _fetch_ohaasa_results(self) -> dict[str, WebSearchResult]:
        now = time.time()
        if self._ohaasa_cache is not None and self._ohaasa_cache[0] > now:
            return self._ohaasa_cache[1]

        request = urllib.request.Request(
            self.config.endpoint,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
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
            LOGGER.warning("Ohaasa horoscope request failed: %s", exc)
            return {}

        results = _parse_ohaasa_results(body)
        if results:
            self._ohaasa_cache = (now + self.config.cache_ttl_seconds, results)
        return results

    def _fetch_daysaju_results(self) -> dict[str, WebSearchResult]:
        now = time.time()
        if self._daysaju_cache is not None and self._daysaju_cache[0] > now:
            return self._daysaju_cache[1]

        request = urllib.request.Request(
            self.config.endpoint,
            headers={
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "callroo-printer/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
        except (
            urllib.error.URLError,
            UnicodeDecodeError,
            TimeoutError,
            ValueError,
        ) as exc:
            LOGGER.warning("Daysaju zodiac request failed: %s", exc)
            return {}

        results = _parse_daysaju_results(body)
        if results:
            self._daysaju_cache = (now + self.config.cache_ttl_seconds, results)
        return results

    def _api_key(self) -> str:
        api_key = getattr(self.config, "api_key", None)
        if api_key:
            return api_key
        return os.environ.get(self.config.api_key_env or "")


def _parse_brave_results(
    results: object,
    *,
    count: int,
) -> tuple[WebSearchResult, ...]:
    if not isinstance(results, list):
        return ()
    parsed_results: list[WebSearchResult] = []
    for item in results[:count]:
        if not isinstance(item, dict):
            continue
        title = _strip_tags(str(item.get("title", "")).strip())
        url = str(item.get("url", "")).strip()
        desc = _strip_tags(str(item.get("description", "")).strip())
        if title or desc or url:
            parsed_results.append(
                WebSearchResult(title=title, url=url, description=desc)
            )
    return tuple(parsed_results)


def _parse_tavily_results(
    results: object,
    *,
    count: int,
) -> tuple[WebSearchResult, ...]:
    if not isinstance(results, list):
        return ()
    parsed_results: list[WebSearchResult] = []
    for item in results[:count]:
        if not isinstance(item, dict):
            continue
        title = _strip_tags(str(item.get("title", "")).strip())
        url = str(item.get("url", "")).strip()
        desc = _strip_tags(
            str(
                item.get("content")
                or item.get("raw_content")
                or item.get("description")
                or ""
            ).strip()
        )
        if title or desc or url:
            parsed_results.append(
                WebSearchResult(title=title, url=url, description=desc)
            )
    return tuple(parsed_results)


def _parse_ohaasa_results(payload: object) -> dict[str, WebSearchResult]:
    if not isinstance(payload, list) or not payload:
        return {}
    latest = payload[0]
    if not isinstance(latest, dict):
        return {}
    onair_date = str(latest.get("onair_date", "")).strip()
    details = latest.get("detail")
    if not isinstance(details, list):
        return {}

    results: dict[str, WebSearchResult] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        sign_code = str(item.get("horoscope_st", "")).zfill(2)
        names = _OHAASA_SIGNS.get(sign_code)
        if names is None:
            continue
        sign_key, sign_name, japanese_name = names
        rank = str(item.get("ranking_no", "")).strip()
        text_parts = [
            part.strip()
            for part in str(item.get("horoscope_text", "")).split("\t")
            if part.strip()
        ]
        fortune_text = " / ".join(text_parts)
        title = f"오하아사 {onair_date} {sign_name} {rank}위"
        description = (
            f"{japanese_name} 공식 순위 {rank}위. {fortune_text}"
            if fortune_text
            else f"{japanese_name} 공식 순위 {rank}위."
        )
        results[sign_key] = WebSearchResult(
            title=title,
            url=_OHAASA_PAGE_URL,
            description=description,
        )
    return results


def _parse_daysaju_results(html_text: str) -> dict[str, WebSearchResult]:
    if not html_text:
        return {}

    date_text = _extract_daysaju_date(html_text)
    parser = _DaysajuLinkParser()
    try:
        parser.feed(html_text)
    except ValueError:
        return {}

    results: dict[str, WebSearchResult] = {}
    signs_by_slug = {item[0]: item for item in _DAYSAAJU_SIGNS}
    grade_pattern = "|".join(re.escape(grade) for grade in _DAYSAAJU_GRADES)
    for slug, text in parser.links:
        sign_info = signs_by_slug.get(slug)
        if sign_info is None:
            continue
        _slug, sign_key, sign_name, hanja = sign_info
        cleaned = _normalize_space(text.replace("상세보기", " "))
        match = re.search(
            rf"(?:{re.escape(hanja)}\s+)?{re.escape(sign_name)}\s+"
            rf"(?P<grade>{grade_pattern})\s+(?P<fortune>.+)$",
            cleaned,
        )
        if not match:
            continue
        grade = match.group("grade").strip()
        fortune_text = _normalize_space(match.group("fortune"))
        if not fortune_text:
            continue
        date_prefix = f" {date_text}" if date_text else ""
        results[sign_key] = WebSearchResult(
            title=f"데이사주{date_prefix} {sign_name} {grade}",
            url=urllib.parse.urljoin(_DAYSAAJU_PAGE_URL, f"/fortune/zodiac/{slug}"),
            description=f"{sign_name} {grade}. {fortune_text}",
        )
    return results


def _extract_daysaju_date(html_text: str) -> str:
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html_text)
    if match:
        return match.group(1).strip()
    match = re.search(r"(\d{4}년\s+\d{1,2}월\s+\d{1,2}일)", html_text)
    if match:
        return _normalize_space(match.group(1))
    return ""


class _DaysajuLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._current_slug: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "a" or self._current_slug is not None:
            return
        href = dict(attrs).get("href") or ""
        parsed = urllib.parse.urlparse(href)
        slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if parsed.path.startswith("/fortune/zodiac/") and slug in _DAYSAAJU_SLUGS:
            self._current_slug = slug
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current_slug is None:
            return
        self.links.append(
            (self._current_slug, _normalize_space(" ".join(self._current_text)))
        )
        self._current_slug = None
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_slug is not None:
            self._current_text.append(data)


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clamp_count(value: int) -> int:
    return min(12, max(1, int(value)))


def _extract_ohaasa_sign(query: str) -> str | None:
    normalized = query.replace(" ", "")
    for sign_key, sign_name, japanese_name in _OHAASA_SIGNS.values():
        if sign_key in normalized or sign_name in normalized or japanese_name in normalized:
            return sign_key
    return None


def _extract_daysaju_sign(query: str) -> str | None:
    normalized = query.replace(" ", "")
    for _slug, sign_key, sign_name, hanja in _DAYSAAJU_SIGNS:
        if sign_name in normalized or hanja in normalized:
            return sign_key
    return None


def _ohaasa_result_rank(result: WebSearchResult) -> int:
    match = re.search(r"(\d+)위", result.title)
    if match:
        return int(match.group(1))
    return 999


def _normalize_tavily_search_depth(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in {"advanced", "basic", "fast", "ultra-fast"}:
        return cleaned
    return "basic"


def _normalize_tavily_country(value: str) -> str:
    cleaned = value.strip().lower().replace("_", " ")
    aliases = {
        "": "",
        "kr": "south korea",
        "kor": "south korea",
        "korea": "south korea",
        "south korea": "south korea",
        "us": "united states",
        "usa": "united states",
        "united states": "united states",
        "jp": "japan",
        "jpn": "japan",
        "japan": "japan",
    }
    return aliases.get(cleaned, cleaned)
