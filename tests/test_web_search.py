from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callroo_printer.web_search import WebSearchClient


class WebSearchClientTest(unittest.TestCase):
    def test_tavily_search_posts_json_with_bearer_key(self) -> None:
        client = WebSearchClient(
            SimpleNamespace(
                enabled=True,
                provider="tavily",
                endpoint="https://api.tavily.com/search",
                api_key="tvly-config-key",
                api_key_env="TAVILY_API_KEY",
                count=3,
                search_lang="ko",
                country="KR",
                search_depth="basic",
                include_answer=False,
                include_raw_content=False,
                cache_ttl_seconds=60.0,
                timeout_seconds=1.0,
            )
        )
        response = _FakeHTTPResponse(
            {
                "results": [
                    {
                        "title": "서울 날씨",
                        "url": "https://weather.example/seoul",
                        "content": "맑고 선선함",
                    }
                ]
            }
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            results = client.search("오늘 서울 날씨", count=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "서울 날씨")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.tavily.com/search")
        self.assertEqual(request.headers["Authorization"], "Bearer tvly-config-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["query"], "오늘 서울 날씨")
        self.assertEqual(body["max_results"], 1)
        self.assertEqual(body["search_depth"], "basic")
        self.assertEqual(body["country"], "south korea")

    def test_tavily_search_uses_env_key_when_config_key_is_empty(self) -> None:
        client = WebSearchClient(
            SimpleNamespace(
                enabled=True,
                provider="tavily",
                endpoint="https://api.tavily.com/search",
                api_key=None,
                api_key_env="TAVILY_API_KEY",
                count=3,
                search_lang="ko",
                country="",
                search_depth="advanced",
                include_answer=True,
                include_raw_content=True,
                cache_ttl_seconds=60.0,
                timeout_seconds=1.0,
            )
        )
        response = _FakeHTTPResponse({"results": []})

        with patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-env-key"}):
            with patch("urllib.request.urlopen", return_value=response) as urlopen:
                client.search("검색", count=2)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer tvly-env-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["search_depth"], "advanced")
        self.assertTrue(body["include_answer"])
        self.assertTrue(body["include_raw_content"])
        self.assertNotIn("country", body)

    def test_ohaasa_search_parses_official_rank_for_sign(self) -> None:
        client = WebSearchClient(
            SimpleNamespace(
                enabled=True,
                provider="ohaasa",
                endpoint="https://www.asahi.co.jp/data/ohaasa2020/horoscope.json",
                api_key=None,
                api_key_env="",
                count=12,
                search_lang="ja",
                country="JP",
                search_depth="basic",
                include_answer=False,
                include_raw_content=False,
                cache_ttl_seconds=86400.0,
                timeout_seconds=1.0,
            )
        )
        response = _FakeHTTPResponse(
            [
                {
                    "onair_date": "20260611",
                    "detail": [
                        {
                            "ranking_no": "9",
                            "horoscope_st": "09",
                            "horoscope_text": "サボりたい気持ち\t何事もまじめに\t\t\tステーキハウス",
                        }
                    ],
                }
            ]
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            results = client.search("오하아사 공식 오늘 사수자리 운세 순위", count=12)

        self.assertEqual(len(results), 1)
        self.assertIn("사수자리 9위", results[0].title)
        self.assertIn("射手座 공식 순위 9위", results[0].description)
        self.assertIn("ステーキハウス", results[0].description)
        self.assertEqual(urlopen.call_count, 1)

        cached = client.search("사수자리", count=12)
        self.assertEqual(cached[0].title, results[0].title)
        self.assertEqual(urlopen.call_count, 1)

    def test_daysaju_search_parses_zodiac_fortune_for_sign(self) -> None:
        client = WebSearchClient(
            SimpleNamespace(
                enabled=True,
                provider="daysaju",
                endpoint="https://daysaju.com/fortune/zodiac",
                api_key=None,
                api_key_env="",
                count=12,
                search_lang="ko",
                country="KR",
                search_depth="basic",
                include_answer=False,
                include_raw_content=False,
                cache_ttl_seconds=86400.0,
                timeout_seconds=1.0,
            )
        )
        response = _FakeHTTPTextResponse(
            """
            <html><head>
              <script type="application/ld+json">
                {"datePublished":"2026-06-11"}
              </script>
            </head><body>
              <a href="/fortune/zodiac/rat">
                <span>子</span><span>쥐띠</span><span>소길</span>
                <p>약속을 지키면 신뢰가 쌓여요.</p><span>상세보기</span>
              </a>
              <a href="/fortune/zodiac/horse">
                <span>午</span><span>말띠</span><span>평</span>
                <p>자세를 점검해 보기 좋은 날이에요.</p><span>상세보기</span>
              </a>
              <a href="/fortune/zodiac/sheep">
                <span>未</span><span>양띠</span><span>평</span>
                <p>재정 계획을 세워보기 좋은 날이에요.</p><span>상세보기</span>
              </a>
            </body></html>
            """
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            results = client.search("오늘 말띠 운세", count=12)

        self.assertEqual(len(results), 1)
        self.assertIn("데이사주 2026-06-11 말띠 평", results[0].title)
        self.assertIn("말띠 평", results[0].description)
        self.assertIn("자세를 점검", results[0].description)
        self.assertEqual(results[0].url, "https://daysaju.com/fortune/zodiac/horse")
        self.assertEqual(urlopen.call_count, 1)

        cached = client.search("말띠", count=12)
        self.assertEqual(cached[0].title, results[0].title)
        self.assertEqual(urlopen.call_count, 1)

        sheep_results = client.search("오늘 양띠 운세", count=12)
        self.assertEqual(len(sheep_results), 1)
        self.assertIn("데이사주 2026-06-11 양띠 평", sheep_results[0].title)
        self.assertEqual(
            sheep_results[0].url,
            "https://daysaju.com/fortune/zodiac/sheep",
        )
        self.assertEqual(urlopen.call_count, 1)


class _FakeHTTPResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _FakeHTTPTextResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def __enter__(self) -> "_FakeHTTPTextResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.text.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
