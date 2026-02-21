# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Colombian DIAN invoice (factura electrónica) extractor** — a FastAPI web app that accepts a PDF invoice, parses it with `pdfplumber`, and returns an Excel file with two sheets: `Info` (vendedor/comprador) and `Items` (line items from the invoice table).

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

Use `inspect_pdf.py` to debug raw PDF text and table extraction for a specific invoice file:
```bash
python inspect_pdf.py "path/to/factura.pdf"
```
This prints the full page 1 text and all detected tables — useful when regex patterns or table parsing break for a new invoice format.

## Architecture

- **`main.py`**: All application logic. Three routes:
  - `GET /` — serves `index.html`
  - `POST /uploadfile/` — DIAN invoice PDF → `.xlsx` (sheets: Info, Items)
  - `POST /upload-bank/` — Bank statement PDF → `.xlsx` (sheet: Movimientos)
- **`index.html`**: Two-panel frontend. Left panel: bank statement extractor. Right panel: DIAN invoice extractor. Both use `fetch` to POST the file and trigger a file download. No JS framework.
- **Python 3.9**, Docker image `python:3.9-slim`.

### Key functions in `main.py`

- **`clean_number(value)`**: Converts Colombian-formatted numbers to plain Python numbers. Detects format by presence of both `.` and `,` (Colombian: dots=thousands, comma=decimal) vs only `,` (Colombian no thousands) vs only `.` (standard decimal). Returns `int` if whole number, `float` otherwise.
- **`extract_data_from_pdf(pdf_stream)`**: Regex on full-page text for vendedor/comprador. Table extraction on page 0, row index 2 onward for items. All numeric columns run through `clean_number`.
- **`extract_bank_statement(pdf_stream)`**: Collects all tables across all pages. Two strategies for Entradas/Salidas: (1) detect explicit debit/credit column names (débito, cargo, crédito, abono, etc.); (2) find the column with the most monetary values and split by sign.

## Key Parsing Assumptions

**Invoice:** Regex patterns are tailored to the DIAN electronic invoice format ("Razón Social:", "Nit del Emisor:", "Datos del Adquiriente / Comprador"). The items table has two header rows — data starts at `item_table[2:]`.

**Bank statement:** Generic extractor; works best when the bank PDF has a clean table structure. The first table with ≥3 columns across all pages is treated as the transaction table. If a new bank's column names aren't detected by the keyword strategy, the auto-detect fallback finds the column with the most monetary values and uses `+`/`-` sign to classify.
