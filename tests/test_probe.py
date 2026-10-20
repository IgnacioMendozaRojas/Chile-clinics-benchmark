import datetime as dt
import io
import zipfile
from pathlib import Path

import pandas as pd

from clinics import probe
from clinics.issuers import load_issuers

INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full"
  xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="http://www.cmfchile.cl">92051000</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="D"><xbrli:entity><xbrli:identifier scheme="http://www.cmfchile.cl">92051000</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:unit id="CLP"><xbrli:measure>iso4217:CLP</xbrli:measure></xbrli:unit>
  <ifrs-full:Revenue contextRef="D" unitRef="CLP" decimals="0">140000000000</ifrs-full:Revenue>
  <ifrs-full:Assets contextRef="I" unitRef="CLP" decimals="0">500000000000</ifrs-full:Assets>
  <ifrs-full:TradeAndOtherCurrentReceivables contextRef="I" unitRef="CLP" decimals="0"
    >90000000000</ifrs-full:TradeAndOtherCurrentReceivables>
</xbrli:xbrl>"""


def _zip(instance: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("92051000_202506_C/92051000_202506_C.xbrl", instance)
    return buf.getvalue()


class FakeFilings:
    """Stands in for eqr's CmfFilings: serves one package, writes it to the cache like the real one."""

    def __init__(self, cache: Path, packages: dict):
        self.cache, self.packages = cache, packages

    def _cached(self, rut, year, month) -> Path:
        return self.cache / f"{rut}_{year}{month:02d}.zip"

    def package(self, rut, year, month):
        data = self.packages.get((year, month))
        if data is None:
            raise LookupError("not found")
        self._cached(rut, year, month).write_bytes(data)
        return data


def test_issuers_config_is_complete():
    issuers = load_issuers()
    assert set(issuers) == {"clc", "indisa", "redsalud", "banmedica", "bupa"}
    assert issuers["indisa"].rut_label == "92.051.000-0"
    assert {i.type for i in issuers.values()} == {"provider", "integrated"}
    assert all(i.cmf_status in {"VI", "NV"} for i in issuers.values())


def test_quarters():
    assert probe.last_closed_quarter(dt.date(2026, 10, 20)) == probe.Quarter(2026, 3)
    assert probe.last_closed_quarter(dt.date(2026, 2, 1)) == probe.Quarter(2025, 4)
    qs = probe.quarter_range(probe.Quarter.parse("2025Q3"), probe.Quarter.parse("2026Q2"))
    assert [str(q) for q in qs] == ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
    assert probe.Quarter(2025, 2).end == dt.date(2025, 6, 30)


def test_check_quarter_reads_and_deletes_package(tmp_path):
    issuer = load_issuers()["indisa"]
    fake = FakeFilings(tmp_path, {(2025, 6): _zip(INSTANCE)})
    row = probe.check_quarter(fake, issuer, probe.Quarter(2025, 2))
    assert row["status"] == "xbrl"
    assert row["has_revenue"] and row["has_assets"] and row["has_trade_receivables"]
    assert not list(tmp_path.iterdir())  # nothing left on disk
    missing = probe.check_quarter(fake, issuer, probe.Quarter(2025, 3))
    assert missing["status"] == "missing"


def test_summary_and_matrix_flag_gaps():
    issuers = load_issuers()
    results = pd.DataFrame(
        {
            "issuer": ["indisa"] * 4,
            "rut": ["92.051.000-0"] * 4,
            "period": ["2025Q1", "2025Q2", "2025Q3", "2025Q4"],
            "status": ["xbrl", "missing", "xbrl", "error: ConnectionError"],
        }
    )
    text = probe.summary(results, issuers)
    assert "| Indisa | 92.051.000-0 | provider | 2/4 | 2025Q1 | 2025Q3 |" in text
    assert "2025Q2 (missing)" in text and "2025Q4 (error: ConnectionError)" in text
    assert "●·●!" in probe.matrix(results)
    (notice,) = probe.annotations(results, issuers)
    assert notice.startswith("::notice title=XBRL Indisa 2/4::2025Q1=xbrl, 2025Q2=missing")
