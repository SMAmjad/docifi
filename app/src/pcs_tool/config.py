"""Loading and validating the YAML configuration (document types + settings)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when a configuration file is missing or invalid (message is plain English)."""


def default_config_dir() -> Path:
    """Where the config lives: env override, frozen .exe bundle, or the source tree."""
    env = os.environ.get("PCS_CONFIG_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "config"
    return Path(__file__).resolve().parents[2] / "config"


@dataclass
class DocType:
    id: str
    name: str
    category: str
    guide_rule: int | None
    guide_type_names: list[str]
    classify: dict[str, Any]
    assembly: dict[str, Any]
    fields: dict[str, dict[str, Any]]
    persons: list[dict[str, Any]]
    label: str
    verify: dict[str, Any]
    property_address: str | None = None

    @property
    def mode(self) -> str:
        return self.assembly.get("mode", "contiguous")


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@dataclass
class Config:
    types: dict[str, DocType]
    settings: Settings
    bank_aliases: dict[str, str]
    source_dir: Path

    def type(self, type_id: str) -> DocType:
        return self.types[type_id]


_REQUIRED_TYPE_KEYS = ("name", "category", "classify", "label", "verify")
_FIELD_KINDS = {
    "address", "date", "person_name", "company", "year", "last4", "ssn", "text", "heading",
    "bank", "period_month", "form_number", "id_name", "id_address", "id_dob", "w2_employee_name", "w2_employee_address",
    "tax_primary_name", "tax_spouse_name", "tax_primary_ssn", "tax_spouse_ssn",
    "tax_primary_dob", "tax_spouse_dob", "tax_address", "group_year",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path.name}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level.")
    return data


def load_config(config_dir: Path | None = None) -> Config:
    """Load and validate configuration. Errors name the exact type/field at fault."""
    cdir = Path(config_dir) if config_dir else default_config_dir()
    types_raw = _read_yaml(cdir / "document_types.yaml")
    settings = Settings(_read_yaml(cdir / "rules.yaml"))

    types: dict[str, DocType] = {}
    for tid, spec in (types_raw.get("types") or {}).items():
        missing = [k for k in _REQUIRED_TYPE_KEYS if k not in spec]
        if missing:
            raise ConfigError(f"Document type '{tid}' is missing: {', '.join(missing)}")
        fields = spec.get("fields") or {}
        for fname, fspec in fields.items():
            kind = (fspec or {}).get("kind")
            if kind not in _FIELD_KINDS:
                raise ConfigError(f"Document type '{tid}', field '{fname}': unknown kind '{kind}'.")
        types[tid] = DocType(
            id=tid,
            name=spec["name"],
            category=spec["category"],
            guide_rule=spec.get("guide_rule"),
            guide_type_names=list(spec.get("guide_type_names") or []),
            classify=spec["classify"],
            assembly=spec.get("assembly") or {"mode": "contiguous"},
            fields=fields,
            persons=list(spec.get("persons") or []),
            label=spec["label"],
            verify=spec["verify"],
            property_address=spec.get("property_address"),
        )
    if not types:
        raise ConfigError("No document types are defined in document_types.yaml.")
    aliases = {k.lower(): v for k, v in (types_raw.get("bank_aliases") or {}).items()}
    return Config(types=types, settings=settings, bank_aliases=aliases, source_dir=cdir)
