from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any


@dataclass(frozen=True)
class Threadmark:
    order: int
    category_id: int
    category_name: str
    threadmark_id: str | None
    post_id: str
    title: str
    author: str
    published_at: str | None
    source_url: str
    reader_url: str
    text: str
    word_count: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "Threadmark":
        data: dict[str, Any] = json.loads(line)
        return cls(**data)
