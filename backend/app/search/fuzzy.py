"""
Fuzzy matching with edit distance (Damerau-Levenshtein, "optimal string alignment").

EDIT DISTANCE = the minimum number of single-character edits that turn one
word into another. We count four kinds of edit:

    insertion      machne   -> machine     (insert 'i')
    deletion       machiine -> machine     (delete 'i')
    substitution   machime  -> machine     (m -> n)
    transposition  mahcine  -> machine     (swap 'hc' -> 'ch'; Damerau's addition,
                                            the most common typing error)

DYNAMIC PROGRAMMING
-------------------
d[i][j] = distance between the first i letters of a and the first j letters of b.

    d[i][0] = i,  d[0][j] = j                                (all deletions / insertions)
    d[i][j] = min( d[i-1][j] + 1,                            deletion
                   d[i][j-1] + 1,                            insertion
                   d[i-1][j-1] + (a[i] != b[j]) )            substitution / match
    and, if a[i] == b[j-1] and a[i-1] == b[j]:
              min( d[i][j], d[i-2][j-2] + 1 )                transposition

Cost: O(len(a) x len(b)). We stop a row early once every value in it already
exceeds the allowed distance, so far-off words are rejected quickly.

HOW MUCH TYPO TO ALLOW
----------------------
Short words have many close neighbours ("cat" is 1 edit from bat, car, cut...),
so the allowed distance grows with length:
    1-3 letters: 0      4-6 letters: 1      7+ letters: 2
"""

from collections.abc import Callable, Iterable


def max_distance_for(word: str) -> int:
    n = len(word)
    if n <= 3:
        return 0
    if n <= 6:
        return 1
    return 2


def edit_distance(a: str, b: str, max_dist: int | None = None) -> int:
    """Damerau-Levenshtein (OSA) distance. If max_dist is given and the true
    distance is larger, returns max_dist + 1 (early exit)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if max_dist is not None and abs(la - lb) > max_dist:
        return max_dist + 1
    if la == 0 or lb == 0:
        return max(la, lb)

    prev2: list[int] | None = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (prev2 is not None and i > 1 and j > 1
                    and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if max_dist is not None and min(cur) > max_dist:
            return max_dist + 1
        prev2, prev = prev, cur
    return prev[lb]


def find_similar(
    word: str,
    vocabulary: Iterable[str],
    popularity: Callable[[str], int],
    max_dist: int | None = None,
    limit: int = 5,
) -> list[tuple[str, int]]:
    """Vocabulary words within the allowed distance, best first:
    smallest distance, then most popular (highest document frequency)."""
    max_dist = max_distance_for(word) if max_dist is None else max_dist
    if max_dist == 0:
        return []
    found: list[tuple[int, int, str]] = []
    for candidate in vocabulary:
        if candidate == word or abs(len(candidate) - len(word)) > max_dist:
            continue
        d = edit_distance(word, candidate, max_dist)
        if d <= max_dist:
            pop = popularity(candidate)
            if pop > 0:
                found.append((d, -pop, candidate))
    found.sort()
    return [(w, d) for d, _, w in found[:limit]]
