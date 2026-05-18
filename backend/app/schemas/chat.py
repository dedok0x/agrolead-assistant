from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ConversationResult:
    text: str
    session_id: int
    lead_id: int | None
    status: str
    stage: str
    next_action: str
    state: str = ""
    captured_fields: list[str] = field(default_factory=list)
    known_facts: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    qualification_score: int = 0
    source_channel: str = "web_widget"
    request_type: str | None = None
    provider: str = "fallback"
    model: str = "none"
    done: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
