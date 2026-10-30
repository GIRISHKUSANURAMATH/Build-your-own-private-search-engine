"""
robots.txt support (RFC 9309).

A site's /robots.txt tells crawlers what they may fetch:

    User-agent: *
    Disallow: /private/
    Crawl-delay: 2

We fetch it once per site (scheme + host + port), cache the rules, and check
every URL before requesting it. Parsing uses Python's standard
urllib.robotparser; fetching goes through our own Fetcher, so robots.txt
requests get the same SSRF checks, timeouts and size limits as pages.

What a missing or broken robots.txt means (RFC 9309):
    2xx         parse and obey the rules
    4xx         no rules: everything is allowed (most sites have no robots.txt)
    5xx / error the site may be down: treat as "disallow everything" for now
"""

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class RobotsCache:
    def __init__(self, fetcher, user_agent: str):
        self.fetcher = fetcher
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | bool] = {}

    @staticmethod
    def _origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def _rules(self, url: str) -> RobotFileParser | bool:
        origin = self._origin(url)
        if origin not in self._cache:
            result = self.fetcher.fetch(origin + "/robots.txt", accept_types=None, follow_robots=False)
            if result.ok:
                parser = RobotFileParser()
                parser.parse(result.text.splitlines())
                self._cache[origin] = parser
            elif result.status is not None and 400 <= result.status < 500:
                self._cache[origin] = True     # no robots.txt: allow all
            else:
                self._cache[origin] = False    # server error / unreachable: disallow all
        return self._cache[origin]

    def allowed(self, url: str) -> bool:
        rules = self._rules(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        rules = self._rules(url)
        if isinstance(rules, bool):
            return None
        delay = rules.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
