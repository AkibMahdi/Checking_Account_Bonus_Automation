"""Loading side of the planner: offers from disk, user config from YAML.

Kept separate so planner.py stays a pure function of (offers, config, today).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

OFFERS_DIR = os.path.join(_ROOT, "offers")
CONFIG_PATH = os.path.join(_ROOT, "user-config.yaml")
EXAMPLE_CONFIG_PATH = os.path.join(_ROOT, "user-config.example.yaml")
CONFIG_SCHEMA_PATH = os.path.join(_ROOT, "schema", "user-config.schema.json")

DEFAULTS = {
    "profile": {
        "max_concurrent_accounts": 3,
        "max_hard_pulls_per_6mo": 2,
        "min_bonus_threshold": 150,
        "avoid_banks": [],
        "chexsystems_sensitive": False,
        "allow_business_accounts": False,
        "max_liquid_capital": None,
        "horizon_days": 365,
    },
    "pay_schedule": {"splittable": False, "max_split_accounts": 1},
    "bank_history": [],
    "hard_pulls": [],
}


def load_offers(directory: str = OFFERS_DIR, include_archive: bool = False) -> list[dict]:
    offers = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            if include_archive and name == "archive":
                offers.extend(load_offers(path))
            continue
        if not name.endswith(".json"):
            continue
        with open(path) as fh:
            offer = json.load(fh)
        if offer.get("no_offer"):
            continue
        offers.append(offer)
    return offers


def _merge_defaults(config: dict) -> dict:
    merged = json.loads(json.dumps(config))  # deep copy, config is plain JSON-ish
    for section, defaults in DEFAULTS.items():
        if isinstance(defaults, dict):
            target = merged.setdefault(section, {})
            for key, value in defaults.items():
                target.setdefault(key, value)
        else:
            merged.setdefault(section, defaults)
    return merged


def _stringify_dates(obj):
    """PyYAML turns bare YYYY-MM-DD into datetime.date; the schema wants strings."""
    if isinstance(obj, dict):
        return {k: _stringify_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_dates(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def load_config(path: str | None = None, *, validate: bool = True) -> dict:
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No config at {path}.\n"
            f"  cp {os.path.relpath(EXAMPLE_CONFIG_PATH, _ROOT)} "
            f"{os.path.relpath(CONFIG_PATH, _ROOT)}\n"
            "then edit it. user-config.yaml is gitignored and never leaves your machine."
        )
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required: pip install -r requirements.txt") from exc

    with open(path) as fh:
        raw = _stringify_dates(yaml.safe_load(fh) or {})

    if validate:
        try:
            import jsonschema
        except ImportError:
            print("warning: jsonschema not installed — config not validated", file=sys.stderr)
        else:
            with open(CONFIG_SCHEMA_PATH) as fh:
                schema = json.load(fh)
            errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(raw),
                            key=lambda e: list(e.path))
            if errors:
                lines = "\n".join(
                    f"  {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
                    for e in errors)
                raise ValueError(f"{os.path.basename(path)} is invalid:\n{lines}")

    return _merge_defaults(raw)
