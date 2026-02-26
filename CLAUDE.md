# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A **Colombian document extractor** — a FastAPI web app with three extractors:
1. **Extracto Bancario**: Accepts a bank statement PDF and returns an Excel with `Entradas` / `Salidas` columns.
2. **Factura DIAN**: Accepts a DIAN electronic invoice PDF and returns an Excel with two sheets: `Info` (vendedor/comprador) and `Items` (line items).
3. **Planilla PILA**: Accepts a PILA payroll form PDF and returns an Excel with per-employee social security contributions.

## Running the App

**Locally (development):**
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**With Docker Compose (production-like):**
```bash
docker-compose up --build
```
The app is exposed on host port `3002` (maps to container port `8000`).

## Debugging / Inspecting PDFs

Use `inspect_pdf.py` to debug raw PDF text and table extraction:
```bash
python inspect_pdf.py "path/to/file.pdf"
```
Prints full page 1 text and all detected tables — useful when regex patterns or table parsing break for a new format.

## Architecture

- **`main.py`**: All application logic. Four routes:
  - `GET /` — serves `index.html`
  - `POST /uploadfile/` — DIAN invoice PDF → `.xlsx` (sheets: Info, Items)
  - `POST /upload-bank/` — Bank statement PDF → `.xlsx` (sheet: Movimientos)
  - `POST /upload-planilla/` — Planilla PILA PDF → `.xlsx` (sheet: Planilla)
- **`index.html`**: Three-panel frontend (Extracto Bancario | Factura DIAN | Planilla PILA). Each panel uses `fetch` + `FormData` to POST the PDF and trigger a file download. No JS framework.
- **Python 3.9**, Docker image `python:3.9-slim`.

---

## Key Functions in `main.py`

### `clean_number(value)`
Converts any formatted number string to a Python `int` or `float`. Handles four formats:

| Input | Output | Rule |
|---|---|---|
| `$ 1.225.000` | `1225000` | Multiple dots → all thousands separators |
| `$ 336.050,42` | `336050.42` | Last comma + 2 trailing digits → comma=decimal |
| `$212,700` | `212700` | Last comma + 3 trailing digits → comma=thousands |
| `24,300.00` | `24300` | Last dot > last comma → dot=decimal |

**Disambiguation logic:**
1. If last dot comes after last comma → dot is decimal; commas are thousands. If multiple dots exist, all are thousands (remove all).
2. If last comma comes after last dot → check digits after last comma: **2 digits = decimal** (Colombian `1,00`), **3 digits = thousands** (`$212,700`).

---

### Invoice Extractor

- **`extract_data_from_pdf(pdf_stream)`**: Regex on full-page text for vendedor/comprador fields. Table extraction on page 0, rows from index 2 onward (two header rows). Applies `clean_number` to: `Nro`, `Cantidad`, `Precio unitario`, `Descuento`, `Recargo`, `IVA`, `IVA %`, `INC`, `INC %`, `Total Item`.
- Regex patterns match DIAN fields: `"Razón Social:"`, `"Nit del Emisor:"`, `"Datos del Adquiriente / Comprador"`.

---

### Bank Statement Extractor

**`extract_bank_statement(pdf_stream)`** — tries two strategies in order:

**Strategy 1 — Table-based** (generic banks with clean PDF tables):
- Scans all pages for tables whose header passes `_is_transaction_table()` (must contain keywords from `_TX_HEADER_KEYWORDS`: `fecha`, `descripci`, `movimiento`, `concepto`, `detalle`, `transacci`).
- Requires ≥5 data rows to be considered valid.
- `_process_table_data()` detects Entradas/Salidas by column keywords (débito, cargo, crédito, abono, etc.) or falls back to finding the column with the most monetary values and splitting by sign.

**Strategy 2 — Text-based** (Bancolombia, Davivienda):
- `_parse_text_transactions(full_text)` tries both text parsers and returns whichever finds more rows.
- **Bancolombia** (`_BANCOLOMBIA_PATTERN`): Format `DD/MM DESCRIPCIÓN VALOR SALDO`. US number format (`24,300.00`). Positive value → Entradas, negative → Salidas.
- **Davivienda** (`_DAVIVIENDA_PATTERN`): Format `DD MM OFIC DESCRIPCIÓN $ DÉBITO $ CRÉDITO`. Two explicit amount columns. Handles multi-line descriptions by appending continuation lines.

**`_is_transaction_table(header_row)`**: Rejects summary/credit-card tables (DINERS, VISA, MASTER, etc.) that appear in some bank PDFs by requiring transaction-specific keywords in the header.

---

### Planilla PILA Extractor

**`extract_planilla_pila(pdf_stream)`** — handles two table formats found in PILA PDFs:

| Format | Columns | Pages | Nombre column |
|---|---|---|---|
| `_PLANILLA_COLS_52` | 52 | Page 1 | col[4] |
| `_PLANILLA_COLS_43` | 43 | Pages 2–6 | col[3] |

Page 7 (22-column summary) is ignored automatically (no match).

**Output columns:** `No`, `Tipo`, `ID`, `Nombre`, `Pension_Codigo`, `Pension_Dias`, `Pension_IBC`, `Pension_Aporte`, `Salud_EPS`, `Salud_Dias`, `Salud_IBC`, `Salud_Aporte`, `CCF_Codigo`, `CCF_Dias`, `CCF_IBC`, `CCF_Aporte`, `Riesgo_Codigo`, `Riesgo_Dias`, `Riesgo_IBC`, `Riesgo_Tarifa`, `Riesgo_Aporte`, `Paraf_Dias`, `Paraf_IBC`, `Paraf_Aporte`, `Exonerado`, `Total_Aportes`.

**Processing rules:**
- Monetary fields → `clean_number()`
- Days fields → converted to `int`
- `Riesgo_Tarifa` → strip `%`, convert to `float`
- `Nombre` → replace `\n` with space (multi-line cells in PDF)
- Rows filtered by requiring `No` column to be a digit string

---

## Key Parsing Assumptions

**Invoice:** DIAN electronic invoice format only. Items table has two header rows; data starts at `item_table[2:]`.

**Bank statement:** Bancolombia and Davivienda PDFs use text-based parsing. Other banks with structured tables use table-based parsing. If a bank isn't detected by either strategy, `extract_bank_statement` returns an empty DataFrame and the API returns a 422 error.

**Planilla PILA:** Column positions are hardcoded for the 52-col and 43-col table formats observed in practice. If a planilla uses a different layout, the column maps need to be updated.
