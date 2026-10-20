# Chile Clinics Industry Benchmark

**How hard did Chile's Isapre crisis hit the private clinic operators that report to the CMF — and did it show up first in receivables, impairments or liquidity?**

A self-updating benchmark of Chilean private clinic groups, rebuilt entirely from public filings at the
[CMF](https://www.cmfchile.cl) (Comisión para el Mercado Financiero). I first built this benchmark by hand in Excel
in 2023, as a strategy and investment analyst in the sector. This repository rebuilds it from scratch, from public
sources only, so that it updates itself every quarter.

> **Status: work in progress.** Step 1 (checking that every issuer's XBRL filings can be downloaded automatically)
> is in place. Indicators, validations and figures follow.

## Coverage

| Group | Issuer | RUT | Type | Panel |
|---|---|---|---|---|
| Clínica Las Condes | Clínica Las Condes S.A. | 93.930.000-7 | Provider | Core |
| Indisa | Instituto de Diagnóstico S.A. | 92.051.000-0 | Provider | Core |
| RedSalud | Empresas Red Salud S.A. | 76.020.458-7 | Provider | Core |
| Banmédica | Banmédica S.A. | 96.528.990-9 | Integrated (Isapre + clinics) | Historical |
| Bupa | Bupa Chile S.A. | 76.005.001-6 | Integrated (Isapre + clinics) | Historical |

*Providers* keep their receivables from Isapres on the balance sheet. *Integrated* groups own an Isapre, so
intra-group receivables are eliminated on consolidation: they enter every financial indicator, but are shown
separately in payer-exposure metrics. Banmédica and Bupa no longer file with the CMF, so their series stop at
their last filing — a series ending is not an improvement.

XBRL coverage, quarter by quarter since 2016Q1 (checked with `clinics.probe` from GitHub Actions, Oct-2026):

| Issuer | Quarterly XBRL filings |
|---|---|
| CLC, Indisa, RedSalud | 2016Q1 – 2026Q2, no gaps |
| Banmédica | 2016Q1 – 2024Q2 |
| Bupa Chile | 2016Q1 – 2022Q1 |

## Data layers

| Layer | Source | Automation |
|---|---|---|
| Financial statements | CMF XBRL filings (taxonomy CL-CI, an IFRS extension) | Fully automatic |
| Notes (receivables ageing, impairment allowance, payables) | XBRL where tagged, otherwise the filing PDF | Automatic with per-issuer config |
| Capacity and activity (beds, occupancy, surgeries…) | Tables in each issuer's MD&A (*Análisis Razonado*) | Automatic with per-issuer config |
| Deflator | Consumer price index (IPC) | Automatic |

The CMF XBRL connector is reused from
[equity-research-engine](https://github.com/IgnacioMendozaRojas/equity-research-engine), where it is validated
against SEC filings.

## Check XBRL coverage

```bash
pip install .
python -m clinics.probe --quick     # sample quarters
python -m clinics.probe             # every quarter since 2016
```

Or, without installing anything or leaving files behind:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/IgnacioMendozaRojas/Chile-clinics-benchmark/main/scripts/probe_local.sh) --quick
```

Raw filings are downloaded to a temporary directory and deleted after parsing; only aggregated tables are committed.

## License

MIT
