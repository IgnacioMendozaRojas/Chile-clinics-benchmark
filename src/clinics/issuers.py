"""Issuer configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "issuers.yaml"  # source checkout / editable install
_PACKAGED_CONFIG = Path(__file__).resolve().parent / "_config" / "issuers.yaml"  # copy shipped in the wheel
CONFIG = _REPO_CONFIG if _REPO_CONFIG.exists() else _PACKAGED_CONFIG


@dataclass(frozen=True)
class Issuer:
    key: str
    name: str
    short: str
    rut: int
    dv: str
    type: str
    cmf_status: str
    panel: str
    notes: str = ""

    @property
    def rut_label(self) -> str:
        return f"{self.rut:,}".replace(",", ".") + f"-{self.dv}"


def load_issuers(path: Path = CONFIG) -> dict[str, Issuer]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["issuers"]
    return {key: Issuer(key=key, **{k: v for k, v in spec.items()}) for key, spec in raw.items()}
