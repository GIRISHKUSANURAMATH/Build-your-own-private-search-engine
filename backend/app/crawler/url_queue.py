"""
The crawl frontier: which URLs to visit next.

Breadth-first (FIFO queue): the seed page first, then every page it links to
(depth 1), then their links (depth 2)... BFS reaches the pages closest to the
seed first, which are usually the most important, and makes the depth limit
natural: links found on a page at max_depth are not followed.

The `seen` set holds NORMALIZED URLs, so /a, /a#top and /A/./../a are queued
once. This is the crawler's duplicate-URL detection within a job.
"""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueItem:
    url: str      # normalized
    depth: int


class CrawlQueue:
    def __init__(self, max_depth: int, max_urls: int):
        self.max_depth = max_depth
        self.max_urls = max_urls          # cap on how many URLs may ever be queued
        self._queue: deque[QueueItem] = deque()
        self.seen: set[str] = set()

    def push(self, url: str, depth: int) -> bool:
        """Queue a normalized URL. False if already seen, too deep, or the cap is reached."""
        if depth > self.max_depth or url in self.seen or len(self.seen) >= self.max_urls:
            return False
        self.seen.add(url)
        self._queue.append(QueueItem(url, depth))
        return True

    def pop(self) -> QueueItem | None:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)
