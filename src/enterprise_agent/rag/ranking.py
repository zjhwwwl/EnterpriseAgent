"""Deterministic Chinese character n-grams, BM25 and reciprocal rank fusion."""

import re

from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    result = []
    for segment in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9]+", text.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", segment):
            result.extend(segment)
            result.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        else:
            result.append(segment)
    return result


def bm25_rank(query: str, texts: dict[str, str], limit: int = 30) -> list[tuple[str, float]]:
    ids = list(texts)
    if not ids:
        return []
    corpus = [tokenize(texts[identifier]) or ["__empty__"] for identifier in ids]
    scores = BM25Okapi(corpus).get_scores(tokenize(query))
    results = [
        (identifier, float(score))
        for identifier, score in zip(ids, scores, strict=True)
        if score > 0
    ]
    return sorted(results, key=lambda item: (-item[1], item[0]))[:limit]


def reciprocal_rank_fusion(*rankings: list[str], k: int = 60) -> list[tuple[str, float]]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(dict.fromkeys(ranking), start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
