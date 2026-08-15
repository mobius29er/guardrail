"""Domain packs — the probes for one domain, plus the policy they assume.

A pack is the unit someone actually wants: "test my Catholic assistant", not
"run these nine YAML files". It bundles the suites, the system prompt those
suites were written against, and the provenance of the cases.

Provenance is a first-class field rather than a footnote. A pack derived from a
real adversarial session and a pack authored from an invariant checklist are
both useful, but a 96% on the second one means considerably less, and the
person reading the score deserves to know which they are holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Provenance values and what they promise.
PROVENANCE: dict[str, str] = {
    "red-team-session": "derived from a real adversarial session",
    "authored": "written from the invariant structure, never fired at a live system",
    "community": "contributed, review before trusting a passing score",
}


@dataclass
class Pack:
    name: str
    title: str
    description: str = ""
    provenance: str = "authored"
    provenance_note: str = ""
    policy: str | None = None
    suites: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    notes: str = ""
    path: str = ""

    @property
    def provenance_label(self) -> str:
        return PROVENANCE.get(self.provenance, self.provenance)

    @property
    def reviewed(self) -> bool:
        """Whether a passing score should be taken at face value."""
        return self.provenance == "red-team-session"

    @classmethod
    def from_yaml(cls, raw: dict[str, Any], path: str = "") -> Pack:
        if "name" not in raw:
            raise ValueError(f"{path or 'pack'}: missing required key 'name'")
        suites = [str(s) for s in raw.get("suites", [])]
        if not suites:
            raise ValueError(f"{path or raw['name']}: pack lists no suites")
        return cls(
            name=str(raw["name"]),
            title=str(raw.get("title", raw["name"])),
            description=str(raw.get("description", "")).strip(),
            provenance=str(raw.get("provenance", "authored")),
            provenance_note=str(raw.get("provenance_note", "")).strip(),
            policy=str(raw["policy"]) if raw.get("policy") else None,
            suites=suites,
            data=[str(d) for d in raw.get("data", [])],
            notes=str(raw.get("notes", "")).strip(),
            path=path,
        )


def packs_dir(root: Path) -> Path:
    return root / "packs"


def available_packs(root: Path) -> list[Pack]:
    """Every pack shipped under ``root``, sorted by name. Never raises."""
    directory = packs_dir(root)
    if not directory.is_dir():
        return []
    found: list[Pack] = []
    for p in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            found.append(Pack.from_yaml(raw, path=p.name))
        except (ValueError, yaml.YAMLError):
            # A malformed pack must not take the whole listing down with it.
            continue
    return found


def load_pack(root: Path, name: str) -> Pack:
    for pack in available_packs(root):
        if pack.name == name:
            return pack
    known = ", ".join(p.name for p in available_packs(root)) or "none installed"
    raise ValueError(f"unknown pack {name!r}. Available: {known}")


def resolve_suites(root: Path, pack: Pack) -> list[str]:
    """Absolute paths to the pack's suite files, in declared order."""
    missing = [s for s in pack.suites if not (root / s).is_file()]
    if missing:
        raise ValueError(f"pack {pack.name!r} references missing suite(s): {', '.join(missing)}")
    return [str(root / s) for s in pack.suites]
