from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vcity.domain.departments import Department

HERE = Path(__file__).parent
FIRST_AUDIO_CEILING = 2.0


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mentions: tuple[str, ...] = ()
    transfer: Department | None = None


class Aside(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wav: Path
    after: float


class AudioCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    wav: Path
    says: str
    aside: Aside | None = None
    expect: Expect = Field(default_factory=Expect)


def load_cases(path: Path = HERE / "cases.yaml") -> list[AudioCase]:
    return [AudioCase.model_validate(raw) for raw in yaml.safe_load(path.read_text("utf-8"))]
