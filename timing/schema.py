from dataclasses import dataclass, field, asdict
from uuid import uuid4


@dataclass
class Unit:
    text: str
    spoken: str
    reading: str = ""
    start: float | None = None
    end: float | None = None
    flags: list[str] = field(default_factory=list)
    source_pronunciation: str = ""


@dataclass
class Line:
    text: str
    start: float | None = None
    end: float | None = None
    agent: str = "v1"
    role: str = "main"
    parent: int | None = None
    language: str = "auto"
    translation: str = ""
    roman: str = ""
    words: list[Unit] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def serialize(lines: list[Line]):
    return [asdict(line) for line in lines]
