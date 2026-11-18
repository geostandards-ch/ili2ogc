"""Load spec/grammar/mapping/*.yml into a single {rule_name: SpecEntry} dict."""

from pathlib import Path

import yaml

from interlis.spec.models import SpecEntry


def load_raw_spec(spec_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Load the mapping files into one {rule: raw entry} dict.

    Also returns cross-file duplicate-rule messages (the first occurrence wins) instead of raising, so a validator
    can report every problem in one pass.
    """
    merged: dict[str, dict] = {}
    origin: dict[str, str] = {}
    duplicates: list[str] = []
    for path in sorted(spec_dir.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for rule, entry in data.items():
            if rule in merged:
                duplicates.append(f"{rule}: present in both {origin[rule]} and {path.name} (cross-file duplicate)")
                continue
            merged[rule] = entry
            origin[rule] = path.name
    return merged, duplicates


def load_spec(spec_dir: Path) -> dict[str, SpecEntry]:
    """Load and validate the mapping files; raises ValidationError on a schema problem, ValueError on a duplicate."""
    raw, duplicates = load_raw_spec(spec_dir)
    if duplicates:
        raise ValueError("; ".join(duplicates))
    return {rule: SpecEntry(**entry) for rule, entry in raw.items()}
