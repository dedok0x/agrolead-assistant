import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from .models import KnowledgeArticle


@dataclass(slots=True)
class RAGChunk:
    article_id: int
    code: str
    title: str
    snippet: str
    score: float


def _tokenize(text: str) -> set[str]:
    normalized = (text or "").lower()
    return {item for item in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", normalized)}


def _clean_text(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"`{1,3}", "", value)
    value = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", value)
    value = re.sub(r"\s+", " ", value)
    return value


def _article_snippet(article: KnowledgeArticle, max_len: int = 320) -> str:
    base = article.short_answer or article.content_markdown or ""
    cleaned = _clean_text(base)
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def retrieve_knowledge_context(
    session: Session,
    *,
    query_text: str,
    request_type_id: Optional[int],
    commodity_id: Optional[int],
    article_group: Optional[str] = None,
    top_k: int = 4,
) -> list[RAGChunk]:
    rows = session.exec(select(KnowledgeArticle).where(KnowledgeArticle.is_active == True)).all()
    if article_group:
        rows = [row for row in rows if row.article_group == article_group]
    if not rows:
        return []

    tokens = _tokenize(query_text)
    scored: list[RAGChunk] = []
    for row in rows:
        title = (row.title or "").lower()
        short = (row.short_answer or "").lower()
        content = (row.content_markdown or "").lower()

        score = 0.0
        for token in tokens:
            if token in title:
                score += 3.0
            if token in short:
                score += 2.0
            if token in content:
                score += 1.0

        if request_type_id and row.request_type_id:
            score += 3.0 if row.request_type_id == request_type_id else -0.5
        elif row.request_type_id is None:
            score += 0.3

        if commodity_id and row.commodity_id:
            score += 2.5 if row.commodity_id == commodity_id else -0.5
        elif row.commodity_id is None:
            score += 0.2

        if not tokens:
            score += max(0.1, (300 - row.sort_order) / 1000)

        if score <= 0:
            continue

        article_id = row.id or 0
        scored.append(
            RAGChunk(
                article_id=article_id,
                code=row.code,
                title=row.title,
                snippet=_article_snippet(row),
                score=score,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[: max(1, min(top_k, 8))]


def render_rag_lines(chunks: list[RAGChunk], max_items: int = 4) -> list[str]:
    lines: list[str] = []
    for item in chunks[: max(1, min(max_items, 8))]:
        lines.append(f"{item.title}: {item.snippet}")
    return lines
