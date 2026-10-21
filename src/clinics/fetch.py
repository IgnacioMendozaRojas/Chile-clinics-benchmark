"""Download issuers' XBRL filings with the reused `eqr` connector, never keeping them on disk.

Every package is written by `CmfFilings` to its cache, read back and deleted at once, so
a run leaves no raw filings behind. Callers get the instance bytes in memory.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .issuers import Issuer


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    q: int

    @property
    def month(self) -> int:
        return 3 * self.q

    @property
    def end(self) -> dt.date:
        return (pd.Timestamp(self.year, self.month, 1) + pd.offsets.MonthEnd(0)).date()

    def __str__(self) -> str:
        return f"{self.year}Q{self.q}"

    def shift(self, quarters: int) -> Quarter:
        n = self.year * 4 + (self.q - 1) + quarters
        return Quarter(n // 4, n % 4 + 1)

    @classmethod
    def parse(cls, text: str) -> Quarter:
        year, q = str(text).upper().split("Q")
        if int(q) not in (1, 2, 3, 4):
            raise ValueError(f"bad quarter {text!r}")
        return cls(int(year), int(q))

    @classmethod
    def of(cls, date: dt.date) -> Quarter:
        return cls(date.year, (date.month - 1) // 3 + 1)


def last_closed_quarter(today: dt.date) -> Quarter:
    return Quarter.of(today).shift(-1)


def quarter_range(start: Quarter, end: Quarter) -> list[Quarter]:
    out, q = [], start
    while q <= end:
        out.append(q)
        q = q.shift(1)
    return out


def filings_for(issuer: Issuer, workdir: Path):
    """A `CmfFilings` pointed at the right registry page (vigente / no vigente) for this issuer."""
    from eqr.data.cmf import CmfFilings

    filings = CmfFilings(Path(workdir) / issuer.key)
    filings.entity_params = {**filings.entity_params, "vig": issuer.cmf_status}
    return filings


RETRY_WAITS = (3, 10)  # seconds before the 2nd and 3rd attempt


def download(filings, issuer: Issuer, quarter: Quarter, waits=None) -> tuple[str, bytes | None, int]:
    """(status, package bytes or None, size in bytes). Never raises; deletes the cached file.

    The CMF site occasionally fails to serve a package that exists, so a missing or failed
    download is retried before being reported.
    """
    waits = RETRY_WAITS if waits is None else waits
    status, data = "missing", None
    for attempt in range(len(waits) + 1):
        if attempt:
            time.sleep(waits[attempt - 1])
        try:
            data = filings.package(issuer.rut, quarter.year, quarter.month)
            status = "ok"
        except (LookupError, FileNotFoundError):
            data, status = None, "missing"
        except Exception as err:  # network or HTTP error: report, keep going
            data, status = None, f"error: {type(err).__name__}"
        if data is not None:
            break
    cached = filings._cached(issuer.rut, quarter.year, quarter.month)
    size = cached.stat().st_size if cached.exists() else 0
    cached.unlink(missing_ok=True)
    return status, data, size


def iter_instances(
    issuers: list[Issuer], quarters: list[Quarter], workdir: Path, pause: float = 0.5
) -> Iterator[tuple[Issuer, Quarter, str, bytes | None]]:
    """Yield (issuer, quarter, status, instance bytes) for every issuer x quarter."""
    from eqr.data import xbrl

    for issuer in issuers:
        filings = filings_for(issuer, workdir)
        last = Quarter.parse(issuer.last_filing) if issuer.last_filing else None
        for quarter in quarters:
            if last and quarter > last:
                continue  # issuer no longer files
            status, data, _ = download(filings, issuer, quarter)
            instance = None
            if data is not None:
                try:
                    instance = xbrl.instance_from_zip(data)
                except Exception as err:
                    status = f"unreadable: {type(err).__name__}"
            print(f"  {issuer.short:<11} {quarter}  {status}", file=sys.stderr, flush=True)
            yield issuer, quarter, status, instance
            time.sleep(pause)
