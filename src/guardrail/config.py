"""Loading target configs and suites from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from guardrail.models import Suite


@dataclass
class TargetConfig:
    """A model under test, plus the optional judge used to grade it."""

    name: str
    provider: dict[str, Any]
    system: str | None = None
    judge: dict[str, Any] | None = None
    concurrency: int = 4
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return str(self.provider.get("model", "unknown"))

    @classmethod
    def load(cls, path: str | Path) -> TargetConfig:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"target config not found: {p}")

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{p}: target config must be a YAML mapping")

        provider = raw.get("provider")
        if not isinstance(provider, dict):
            raise ValueError(f"{p}: target config must define a 'provider' mapping")

        system = raw.get("system")
        system_file = raw.get("system_file")
        if system_file:
            sp = (p.parent / str(system_file)).resolve()
            if not sp.is_file():
                raise FileNotFoundError(f"{p}: system_file not found: {sp}")
            system = sp.read_text(encoding="utf-8")

        judge = raw.get("judge")
        if judge is not None and not isinstance(judge, dict):
            raise ValueError(f"{p}: 'judge' must be a mapping if present")

        concurrency = int(raw.get("concurrency", 4))
        if concurrency < 1:
            raise ValueError(f"{p}: concurrency must be >= 1")

        return cls(
            name=str(raw.get("name", p.stem)),
            provider=provider,
            system=system,
            judge=judge,
            concurrency=concurrency,
            metadata=raw.get("metadata", {}) or {},
        )


def load_suites(paths: list[str | Path]) -> list[Suite]:
    """Load suites from files and/or directories.

    A directory is scanned non-recursively for ``*.yaml`` / ``*.yml``. Results
    are sorted by path so runs are reproducible.
    """
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(q for q in p.iterdir() if q.suffix in (".yaml", ".yml")))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"suite path not found: {p}")

    if not files:
        raise ValueError(f"no suite files found in {[str(p) for p in paths]}")

    suites: list[Suite] = []
    for f in files:
        raw_doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_doc, dict):
            raise ValueError(f"{f}: suite must be a YAML mapping")
        try:
            suites.append(Suite.from_yaml(raw_doc, path=str(f)))
        except ValueError as exc:
            raise ValueError(f"{f}: {exc}") from exc
    return suites
