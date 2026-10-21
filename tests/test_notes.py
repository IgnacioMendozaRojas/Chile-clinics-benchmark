import pytest

from clinics import notes
from clinics.facts import consolidated_facts, parse
from clinics.fetch import Quarter
from clinics.inventory import dimension_members

CTX = """<xbrli:context id="{id}"><xbrli:entity><xbrli:identifier scheme="http://www.cmfchile.cl">92051000</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period>{period}</xbrli:period></xbrli:context>"""


def period(date: str) -> str:
    # "YTD:2025-06-30" -> a duration from 1 January (how the stratification note is tagged)
    if date.startswith("YTD:"):
        end = date[4:]
        return f"<xbrli:startDate>{end[:4]}-01-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
    return f"<xbrli:instant>{date}</xbrli:instant>"


FACTS = [
    # (axis, member, concept, value, date)
    ("cl-ci:TramosMorosidadEje", "cl-ci:AlDiaMiembro", "cl-ci:CarteraBruta", 60, "2025-06-30"),
    ("cl-ci:TramosMorosidadEje", "cl-ci:Entre1Y30DiasMiembro", "cl-ci:CarteraBruta", 15, "2025-06-30"),
    ("cl-ci:TramosMorosidadEje", "cl-ci:Entre181Y210DiasMiembro", "cl-ci:CarteraBruta", 10, "2025-06-30"),
    ("cl-ci:TramosMorosidadEje", "cl-ci:MasDe250DiasMiembro", "cl-ci:CarteraBruta", 15, "YTD:2025-06-30"),
    ("cl-ci:TramosMorosidadEje", "cl-ci:AlDiaMiembro", "cl-ci:CarteraBruta", 999, "2024-12-31"),  # comparative
    (
        "cl-ci:ActivosPorDeudoresComercialesEje",
        "cl-ci:ActivosAntesProvisionesMiembro",
        "ifrs-full:TradeAndOtherCurrentReceivables",
        100,
        "2025-06-30",
    ),
    (
        "cl-ci:ActivosPorDeudoresComercialesEje",
        "cl-ci:ProvisionesDeudoresComercialesMiembro",
        "ifrs-full:TradeAndOtherCurrentReceivables",
        -12,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasAlDiaPorTipoProveedorEje",
        "cl-ci:CuentasAlDiaProveedorBienesMiembro",
        "cl-ci:CuentasComercialesAlDia",
        50,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasAlDiaPorTipoProveedorEje",
        "cl-ci:CuentasAlDiaProveedorServiciosMiembro",
        "cl-ci:CuentasComercialesAlDia",
        30,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasAlDiaPorTipoProveedorEje",
        "cl-ci:CuentasAlDiaProveedorBienesMiembro",
        "cl-ci:CuentasComercialesAlDiaHasta30Dias",
        50,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasAlDiaPorTipoProveedorEje",
        "cl-ci:CuentasAlDiaProveedorServiciosMiembro",
        "cl-ci:CuentasComercialesAlDiaHasta30Dias",
        30,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasPlazosVencidosPorTipoProveedorEje",
        "cl-ci:CuentasPlazosVencidosProveedorBienesMiembro",
        "cl-ci:CuentasComercialesVencidas",
        20,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasPlazosVencidosPorTipoProveedorEje",
        "cl-ci:CuentasPlazosVencidosProveedorBienesMiembro",
        "cl-ci:CuentasComercialesVencidasEntre1Y30Dias",
        12,
        "2025-06-30",
    ),
    (
        "cl-ci:CuentasPlazosVencidosPorTipoProveedorEje",
        "cl-ci:CuentasPlazosVencidosProveedorBienesMiembro",
        "cl-ci:CuentasComercialesVencidasMasDe180Dias",
        8,
        "2025-06-30",
    ),
    ("cl-ci:LeasingEje", "cl-ci:Arriendo1Miembro", "ifrs-full:LeaseLiabilities", 7, "2025-06-30"),  # other axis
]


def instance() -> bytes:
    ctx = "".join(CTX.format(id=f"c{i}", axis=a, member=m, period=period(d)) for i, (a, m, _, _, d) in enumerate(FACTS))
    facts = "".join(f'<{c} contextRef="c{i}" unitRef="CLP">{v}</{c}>' for i, (_, _, c, v, _) in enumerate(FACTS))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full" xmlns:cl-ci="http://www.cmfchile.cl/cl/fr/ci"
  xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="http://www.cmfchile.cl">92051000</xbrli:identifier>
</xbrli:entity><xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
{ctx}<xbrli:unit id="CLP"><xbrli:measure>iso4217:CLP</xbrli:measure></xbrli:unit>
<ifrs-full:Assets contextRef="I" unitRef="CLP">500</ifrs-full:Assets>
{facts}</xbrli:xbrl>""".encode()


@pytest.fixture
def table():
    parsed = parse(instance())
    cfg = notes.load_notes()
    facts = notes.note_facts(parsed, "indisa", Quarter(2025, 2), cfg)
    assert set(facts["axis"]) == {n.axis for n in cfg.values()}  # LeasingEje dropped
    assert 999 not in facts["value"].tolist()  # comparative date dropped
    return notes.build(facts, cfg)


def test_consolidated_facts_ignore_dimensional_facts():
    f = consolidated_facts(parse(instance()), "indisa", Quarter(2025, 2))
    assert f["concept"].tolist() == ["ifrs-full:Assets"]


def test_build_sums_supplier_types_and_makes_allowance_positive(table):
    v = table.set_index(["note", "item"])["value"]
    assert v[("payables_current", "total")] == 80  # goods 50 + services 30
    assert v[("receivables_allowance", "allowance")] == 12
    assert v[("receivables_ageing", "d250_plus")] == 15


def test_note_indicators(table):
    ind = notes.compute_indicators(table).set_index("indicator")["value"]
    assert ind["receivables_overdue_share"] == pytest.approx(0.40)  # 1 - 60/100
    assert ind["receivables_over_180_share"] == pytest.approx(0.25)  # (10 + 15) / 100
    assert ind["allowance_coverage"] == pytest.approx(0.12)
    assert ind["payables_overdue_share"] == pytest.approx(20 / 100)
    assert ind["payables_over_90_share"] == pytest.approx(8 / 100)  # buckets 12 + 8 = total 20


def test_over_90_withheld_when_overdue_buckets_incomplete(table):
    t = table[~((table["note"] == "payables_overdue") & (table["item"] == "d001_030"))]
    ind = notes.compute_indicators(t).set_index("indicator")["value"]
    assert "payables_over_90_share" not in ind and "payables_overdue_share" in ind


def test_checks_flag_inconsistent_buckets(table):
    assert notes.checks(table).empty
    t = table.copy()
    t.loc[(t["note"] == "payables_current") & (t["item"] == "d000_030"), "value"] = 60
    found = notes.checks(t)
    assert found["check"].tolist() == ["payables_buckets"]


def test_inventory_uses_connector_members():
    members = dimension_members(parse(instance()))
    assert ("cl-ci:CarteraBruta", "cl-ci:TramosMorosidadEje", "cl-ci:AlDiaMiembro", "instant") in members
    assert ("cl-ci:CarteraBruta", "cl-ci:TramosMorosidadEje", "cl-ci:MasDe250DiasMiembro", "duration") in members
