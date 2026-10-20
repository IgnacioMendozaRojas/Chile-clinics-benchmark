# Chile Clinics Industry Benchmark

**How hard did Chile's Isapre crisis hit the private clinic operators that report to the CMF — and did it show up first in receivables, impairments or liquidity?**

A self-updating benchmark of Chilean private clinic groups, rebuilt entirely from public filings at the
[CMF](https://www.cmfchile.cl) (Comisión para el Mercado Financiero). I first built this benchmark by hand in Excel
in 2023, as a strategy and investment analyst in the sector. This repository rebuilds it from scratch, from public
sources only, so that it updates itself every quarter.

> **Status: work in progress.** Financial statements and financial indicators are built automatically from XBRL
> and validated (see [`data/processed`](data/processed)). Notes (receivables ageing, allowances, overdue payables),
> capacity and activity, real terms, figures and the project page follow.

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

## Published tables

Rebuilt from scratch by [`update.yml`](.github/workflows/update.yml) on the 1st and 16th of each month (and on
demand), committed only when every validation passes and something changed.

| File | Contents |
|---|---|
| [`statements.csv`](data/processed/statements.csv) | Issuer × quarter × variable, in CLP: balances, year-to-date flows as filed, the quarter, and last twelve months (LTM) |
| [`indicators.csv`](data/processed/indicators.csv) | Issuer × quarter × indicator, with issuer type and panel |
| [`restatements.csv`](data/processed/restatements.csv) | Figures a later filing presents differently: as reported vs revised |
| [`validation_report.md`](data/processed/validation_report.md) | Errors, warnings and restatements found in the latest run |
| [`coverage.csv`](data/processed/coverage.csv) | Which quarters each issuer filed |

### Method

- **As reported.** Every figure comes from the filing of its own period. When a later filing restates a period in
  its comparative columns, the revision is logged in `restatements.csv` and the period is flagged (`restated`), but
  not substituted: substituting would mix accounting bases inside quarterly and LTM figures (a restated full year
  minus nine months that were never restated). Example: in its December 2025 filing CLC restated December 2024 trade
  receivables from CLP 85.2 bn to 26.0 bn and equity from 204.1 bn to 136.6 bn.
- **Figures** plot the as-reported series. Periods a later filing restated are marked, and the revised figure is
  shown as a separate point, so the size of each restatement is visible without breaking the series.
- **Quarters and LTM.** Chilean filings report flows year to date; quarter = YTD(q) − YTD(q−1);
  LTM = YTD(q) + FY(y−1) − YTD(q, y−1). Flow-based ratios use LTM to remove seasonality.
- **Concepts.** Each variable maps to the CL-CI/IFRS tags in [`config/concepts.yaml`](config/concepts.yaml),
  checked against the concepts all five issuers actually file. Costs are taken in absolute value (filers use both
  sign conventions).
- **Definitions.** Operating result (EBIT) = gross profit − distribution − administrative expenses − credit losses
  presented on the face of the income statement (so issuers that show IFRS 9 losses separately and those that
  include them in expenses are measured alike). EBITDA adds depreciation and amortisation, including right-of-use
  assets from 2019 (IFRS 16 is a level break for every issuer). Gross financial debt = other financial
  liabilities + lease liabilities; net debt subtracts cash. All ratios are nominal: both sides are from the same
  period, so inflation cancels.
- **Validations.** Assets = liabilities + equity (error above 0.1%: nothing is published), assets = current +
  non-current, gross profit and net income arithmetic, missing core items, quarter-on-quarter jumps, restatements.

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
