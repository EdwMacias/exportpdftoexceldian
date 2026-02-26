from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import pdfplumber
import pandas as pd
import io
import re

app = FastAPI()


def clean_number(value):
    """Convert a number string to a Python int or float.

    Handles both formats automatically using the "last separator wins" rule:
    - Colombian/European (dot=thousands, comma=decimal): '$ 1.234.567,89' → 1234567.89
    - US/Bancolombia (comma=thousands, dot=decimal):     '1,234,567.89'   → 1234567.89
    - Partial:  '.86' → 0.86,  '1,00' → 1,  '19.00' → 19
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    s = str(value).replace('$', '').strip()
    if not s or s == '-':
        return None

    last_dot = s.rfind('.')
    last_comma = s.rfind(',')

    if last_dot > last_comma:
        # Dot is last separator (or only separator)
        if s.count('.') > 1:
            # Multiple dots → all are thousands separators: '1.225.000' → 1225000
            s = s.replace('.', '').replace(',', '')
        else:
            # Single dot → decimal point: '24,300.00' → 24300.00
            s = s.replace(',', '')
    elif last_comma > last_dot:
        # Comma is last separator — disambiguate by digits after the last comma:
        #   2 trailing digits  → decimal  (Colombian/European): '1,00' → 1.00
        #   3 trailing digits  → thousands (US / peso amounts): '212,700' → 212700
        after = s[last_comma + 1:]
        if len(after) == 2 and after.isdigit():
            # Treat comma as decimal separator
            s = s.replace('.', '').replace(',', '.')
        else:
            # Treat comma as thousands separator
            s = s.replace(',', '')
    # else: no separator → plain integer, keep as-is

    try:
        num = float(s)
        return int(num) if num == int(num) else num
    except (ValueError, TypeError):
        return value


# ─────────────────────────────────────────────
# INVOICE EXTRACTOR
# ─────────────────────────────────────────────

def extract_data_from_pdf(pdf_stream):
    vendedor = {}
    comprador = {}
    items = []

    numeric_columns = {
        "Nro", "Cantidad", "Precio unitario", "Descuento",
        "Recargo", "IVA", "IVA %", "INC", "INC %", "Total Item"
    }

    with pdfplumber.open(pdf_stream) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

        # Extract Vendedor
        vendedor_razon_social = re.search(r"Razón Social: (.*?)\n", full_text)
        vendedor_nit = re.search(r"Nit del Emisor: (.*?)\n", full_text)
        if vendedor_razon_social and vendedor_nit:
            vendedor = {
                "Razón Social": vendedor_razon_social.group(1).strip(),
                "NIT": vendedor_nit.group(1).strip(),
            }

        # Extract Comprador
        comprador_razon_social = re.search(
            r"Datos del Adquiriente / Comprador\nNombre o Razón Social: (.*?)\n", full_text
        )
        comprador_nit = re.search(r"Número Documento: (.*?)\n", full_text)
        if comprador_razon_social and comprador_nit:
            comprador = {
                "Razón Social": comprador_razon_social.group(1).strip(),
                "NIT": comprador_nit.group(1).strip(),
            }

        # Extract Items — page 0, first table, data starts at row index 2
        page = pdf.pages[0]
        tables = page.extract_tables()
        if tables:
            item_table = tables[0]
            headers = [
                "Nro", "Código", "Descripción", "U/M", "Cantidad",
                "Precio unitario", "Descuento", "Recargo",
                "IVA", "IVA %", "INC", "INC %", "Total Item"
            ]
            for row in item_table[2:]:
                item_data = {}
                for i, header in enumerate(headers):
                    val = row[i] if i < len(row) else None
                    if header in numeric_columns:
                        val = clean_number(val)
                    item_data[header] = val
                items.append(item_data)

    df_info = pd.DataFrame({
        'Tipo': ['Vendedor', 'Comprador'],
        'Razón Social': [vendedor.get('Razón Social'), comprador.get('Razón Social')],
        'NIT': [vendedor.get('NIT'), comprador.get('NIT')]
    })
    df_items = pd.DataFrame(items)
    return {"Info": df_info, "Items": df_items}


# ─────────────────────────────────────────────
# BANK STATEMENT EXTRACTOR
# ─────────────────────────────────────────────

# ── Bancolombia ──────────────────────────────────────────────────────────────
# Lines: "1/04 PAGO QR ERIS DAYID B. 24,300.00 3,888,729.61"
#        "1/04 ABONO INTERESES AHORROS .86 4,751,911.77"
_BANCOLOMBIA_PATTERN = re.compile(
    r'^(\d{1,2}/\d{2})\s+'      # FECHA  e.g. 1/04, 15/06
    r'(.+?)\s+'                  # DESCRIPCIÓN  (non-greedy)
    r'(-?[\d,]*\.[\d]+)\s+'     # VALOR  (possibly negative, possibly ".86")
    r'(-?[\d,]*\.[\d]+)\s*$'    # SALDO
)

# ── Davivienda ───────────────────────────────────────────────────────────────
# Lines: "01 01 9070 Abono ventas netas Mastercard 17015595 $ 0.00 $ 5,534,339.00"
#        "02 01 0033 Pago ENEL PAGO FACTURA ... $ 1,452,470.00 $ 0.00"
_DAVIVIENDA_PATTERN = re.compile(
    r'^(\d{2})\s+(\d{2})\s+\d+\s+'        # DIA MES OFICINA
    r'(.+?)\s+'                             # DESCRIPCIÓN (non-greedy)
    r'\$\s*([\d,]+\.[\d]{2})\s+'           # DÉBITO
    r'\$\s*([\d,]+\.[\d]{2})\s*$'          # CRÉDITO
)

# Lines that are headers/footers in Davivienda — never continuations
_DAVI_SKIP = re.compile(
    r'^(CUENTA DE AHORROS|DAMAS|Banco Davivienda|Fecha|D.a Mes|Oficina|'
    r'H\.\d|Apreciado|Davivienda a partir|Este producto|Cualquier diferencia|'
    r'Recuerde que|Tel.fono|Para mayor|INFORME|Saldo Anterior|M.s Cr.ditos|'
    r'Menos D.bitos|Nuevo Saldo|Saldo Promedio)',
    re.IGNORECASE,
)


def _parse_bancolombia_text(full_text):
    """Parse Bancolombia plain-text statement (single VALOR column + SALDO)."""
    rows = []
    for line in full_text.split('\n'):
        m = _BANCOLOMBIA_PATTERN.match(line.strip())
        if not m:
            continue
        fecha, desc, valor_str, saldo_str = m.groups()
        valor = clean_number(valor_str)
        saldo = clean_number(saldo_str)
        rows.append({
            'Fecha': fecha,
            'Descripción': desc.strip(),
            'Entradas': valor if isinstance(valor, (int, float)) and valor > 0 else None,
            'Salidas': abs(valor) if isinstance(valor, (int, float)) and valor < 0 else None,
            'Saldo': saldo,
        })
    return pd.DataFrame(rows) if rows else None


def _parse_davivienda_text(full_text):
    """Parse Davivienda plain-text statement (separate Débito / Crédito columns)."""
    rows = []
    lines = full_text.split('\n')

    for i, line in enumerate(lines):
        m = _DAVIVIENDA_PATTERN.match(line.strip())
        if not m:
            continue
        dia, mes, desc_raw, deb_str, cre_str = m.groups()

        # Append continuation line to description when the next line is not a new
        # transaction and not a known header/footer.
        desc = desc_raw.strip()
        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if (nxt
                    and not _DAVIVIENDA_PATTERN.match(nxt)
                    and not _DAVI_SKIP.match(nxt)):
                desc = desc + ' ' + nxt

        debito = clean_number(deb_str)
        credito = clean_number(cre_str)

        rows.append({
            'Fecha': f'{dia}/{mes}',
            'Descripción': desc,
            'Entradas': credito if isinstance(credito, (int, float)) and credito > 0 else None,
            'Salidas': debito if isinstance(debito, (int, float)) and debito > 0 else None,
        })

    return pd.DataFrame(rows) if rows else None


def _parse_text_transactions(full_text):
    """Try all known plain-text parsers and return the one with most rows."""
    candidates = [
        _parse_davivienda_text(full_text),
        _parse_bancolombia_text(full_text),
    ]
    candidates = [df for df in candidates if df is not None and not df.empty]
    if not candidates:
        return None
    return max(candidates, key=len)


def _process_table_data(headers, rows):
    """Build DataFrame from table rows and add Entradas/Salidas columns."""
    df = pd.DataFrame(rows, columns=headers)
    col_lower = {col: col.lower() for col in df.columns}

    debit_cols = [
        col for col, lower in col_lower.items()
        if any(k in lower for k in ['débit', 'debito', 'cargo', 'salida', 'egreso', 'retiro'])
    ]
    credit_cols = [
        col for col, lower in col_lower.items()
        if any(k in lower for k in ['crédit', 'credito', 'abono', 'entrada', 'ingreso', 'depósito', 'deposito'])
    ]

    if debit_cols and credit_cols:
        df['Salidas'] = df[debit_cols[0]].apply(clean_number)
        df['Entradas'] = df[credit_cols[0]].apply(clean_number)
    else:
        monetary_pattern = re.compile(r'\d[\d.,]+')
        best_col, best_count = None, 0
        for col in df.columns:
            count = df[col].apply(
                lambda v: bool(monetary_pattern.search(str(v))) if v else False
            ).sum()
            if count > best_count:
                best_count, best_col = count, col

        if best_col:
            def to_signed(val):
                if not val:
                    return None
                s = str(val).strip()
                is_neg = s.startswith('-') or '(' in s
                num = clean_number(re.sub(r'[^\d.,]', '', s))
                return (-num if is_neg else num) if isinstance(num, (int, float)) else None

            df['_amount'] = df[best_col].apply(to_signed)
            df['Entradas'] = df['_amount'].apply(lambda x: x if isinstance(x, (int, float)) and x > 0 else None)
            df['Salidas'] = df['_amount'].apply(lambda x: abs(x) if isinstance(x, (int, float)) and x < 0 else None)
            df = df.drop(columns=['_amount'])

    return df


# Keywords that indicate a table is a transaction table (not a summary table)
_TX_HEADER_KEYWORDS = {'fecha', 'descripci', 'movimiento', 'concepto', 'detalle', 'transacci'}


def _is_transaction_table(header_row):
    """Return True if the header row looks like a financial transaction table."""
    combined = ' '.join(str(h or '').lower() for h in header_row)
    return any(kw in combined for kw in _TX_HEADER_KEYWORDS)


def extract_bank_statement(pdf_stream):
    """Extract transactions from a bank statement PDF.

    Strategy:
    1. Table extraction — only uses tables whose header contains transaction
       keywords (Fecha, Descripción, Movimiento, etc.) to avoid picking up
       summary/totals tables.
    2. Text parsing fallback — if no qualifying transaction table is found,
       tries known plain-text formats (Davivienda, Bancolombia).
    """
    all_table_rows = []
    all_text_parts = []
    headers = None

    with pdfplumber.open(pdf_stream) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text_parts.append(text)

            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                max_cols = max((len(row) for row in table if row), default=0)
                if max_cols < 3:
                    continue

                if headers is None:
                    candidate = [
                        str(h).strip() if h else f"Col{i}"
                        for i, h in enumerate(table[0])
                    ]
                    # Only accept tables whose headers look like transaction tables
                    if not _is_transaction_table(candidate):
                        continue
                    headers = candidate
                    for row in table[1:]:
                        if len(row) == len(headers) and any(c for c in row if c):
                            all_table_rows.append(list(row))
                else:
                    for row in table:
                        if len(row) != len(headers):
                            continue
                        # Skip repeated header rows
                        if str(row[0]).strip() == str(headers[0]).strip():
                            continue
                        if any(c for c in row if c):
                            all_table_rows.append(list(row))

    # Use table data if substantial; otherwise fall back to text parsing
    if all_table_rows and len(all_table_rows) >= 5:
        return _process_table_data(headers, all_table_rows)

    full_text = '\n'.join(all_text_parts)
    return _parse_text_transactions(full_text)


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/uploadfile/")
async def create_upload_file(file: UploadFile = File(...)):
    pdf_content = await file.read()
    with io.BytesIO(pdf_content) as pdf_stream:
        data_frames = extract_data_from_pdf(pdf_stream)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        data_frames["Info"].to_excel(writer, index=False, sheet_name='Info')
        data_frames["Items"].to_excel(writer, index=False, sheet_name='Items')
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=factura_export.xlsx"}
    )


@app.post("/upload-bank/")
async def upload_bank_statement(file: UploadFile = File(...)):
    pdf_content = await file.read()
    with io.BytesIO(pdf_content) as pdf_stream:
        df = extract_bank_statement(pdf_stream)

    if df is None or df.empty:
        return JSONResponse(
            status_code=422,
            content={"error": "No se encontraron transacciones en el PDF."}
        )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Movimientos')
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=extracto_export.xlsx"}
    )


# ─────────────────────────────────────────────
# PLANILLA PILA EXTRACTOR
# ─────────────────────────────────────────────

# Column mapping for the 52-column table (page 1 of the planilla)
_PLANILLA_COLS_52 = {
    'No': 0, 'Tipo': 1, 'ID': 2, 'Nombre': 4,
    'Pension_Codigo': 24, 'Pension_Dias': 25, 'Pension_IBC': 26, 'Pension_Aporte': 29,
    'Salud_EPS': 30, 'Salud_Dias': 31, 'Salud_IBC': 32, 'Salud_Aporte': 33,
    'CCF_Codigo': 35, 'CCF_Dias': 36, 'CCF_IBC': 37, 'CCF_Aporte': 39,
    'Riesgo_Codigo': 42, 'Riesgo_Dias': 43, 'Riesgo_IBC': 44,
    'Riesgo_Tarifa': 45, 'Riesgo_Aporte': 46,
    'Paraf_Dias': 47, 'Paraf_IBC': 48, 'Paraf_Aporte': 49,
    'Exonerado': 50, 'Total': 51,
}

# Column mapping for the 43-column table (pages 2-6)
_PLANILLA_COLS_43 = {
    'No': 0, 'Tipo': 1, 'ID': 2, 'Nombre': 3,
    'Pension_Codigo': 21, 'Pension_Dias': 22, 'Pension_IBC': 23, 'Pension_Aporte': 24,
    'Salud_EPS': 25, 'Salud_Dias': 26, 'Salud_IBC': 27, 'Salud_Aporte': 28,
    'CCF_Codigo': 29, 'CCF_Dias': 30, 'CCF_IBC': 31, 'CCF_Aporte': 32,
    'Riesgo_Codigo': 33, 'Riesgo_Dias': 34, 'Riesgo_IBC': 35,
    'Riesgo_Tarifa': 36, 'Riesgo_Aporte': 37,
    'Paraf_Dias': 38, 'Paraf_IBC': 39, 'Paraf_Aporte': 40,
    'Exonerado': 41, 'Total': 42,
}

_PLANILLA_MONEY = {
    'Pension_IBC', 'Pension_Aporte', 'Salud_IBC', 'Salud_Aporte',
    'CCF_IBC', 'CCF_Aporte', 'Riesgo_IBC', 'Riesgo_Aporte',
    'Paraf_IBC', 'Paraf_Aporte', 'Total',
}
_PLANILLA_INT = {'Pension_Dias', 'Salud_Dias', 'CCF_Dias', 'Riesgo_Dias', 'Paraf_Dias'}

_PLANILLA_RENAME = {
    'No': 'No.', 'ID': 'Identificación',
    'Pension_Codigo': 'Pensión_Código', 'Pension_Dias': 'Pensión_Días',
    'Pension_IBC': 'Pensión_IBC', 'Pension_Aporte': 'Pensión_Aporte',
    'Salud_EPS': 'Salud_EPS', 'Salud_Dias': 'Salud_Días',
    'Salud_IBC': 'Salud_IBC', 'Salud_Aporte': 'Salud_Aporte',
    'CCF_Codigo': 'CCF_Código', 'CCF_Dias': 'CCF_Días',
    'CCF_IBC': 'CCF_IBC', 'CCF_Aporte': 'CCF_Aporte',
    'Riesgo_Codigo': 'Riesgo_Código', 'Riesgo_Dias': 'Riesgo_Días',
    'Riesgo_IBC': 'Riesgo_IBC', 'Riesgo_Tarifa': 'Riesgo_Tarifa%',
    'Riesgo_Aporte': 'Riesgo_Aporte',
    'Paraf_Dias': 'Paraf_Días', 'Paraf_IBC': 'Paraf_IBC', 'Paraf_Aporte': 'Paraf_Aporte',
    'Total': 'Total_Aportes',
}


def extract_planilla_pila(pdf_stream):
    """Extract per-employee data from a Planilla PILA (Colombian social security) PDF.

    Handles two table layouts:
    - 52-column (page 1): wider due to extra novedad columns on the left.
    - 43-column (pages 2+): standard layout for the rest of the document.
    """
    rows = []

    with pdfplumber.open(pdf_stream) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or not table[0]:
                    continue
                ncols = len(table[0])
                if ncols == 52:
                    col_map = _PLANILLA_COLS_52
                elif ncols == 43:
                    col_map = _PLANILLA_COLS_43
                else:
                    continue  # skip summary / unknown tables

                for row in table:
                    if not row or not row[0]:
                        continue
                    # Only employee rows — first cell is a plain integer
                    if not str(row[0]).strip().isdigit():
                        continue

                    record = {}
                    for field, ci in col_map.items():
                        val = row[ci] if ci < len(row) else None
                        if val not in (None, ''):
                            val = str(val).strip()
                            if field == 'Pension_Codigo':
                                # Code and sub-code may be joined with newline; keep first line
                                val = val.split('\n')[0].strip()
                            elif field == 'Riesgo_Tarifa':
                                val = val.replace('%', '').strip()
                                try:
                                    val = float(val)
                                except ValueError:
                                    pass
                            elif field in _PLANILLA_MONEY:
                                val = clean_number(val)
                            elif field in _PLANILLA_INT:
                                try:
                                    val = int(val.split('\n')[0].strip())
                                except ValueError:
                                    pass
                            else:
                                val = val.replace('\n', ' ')
                        else:
                            val = None
                        record[field] = val

                    rows.append(record)

    if not rows:
        return None

    df = pd.DataFrame(rows).rename(columns=_PLANILLA_RENAME)
    return df


@app.post("/upload-planilla/")
async def upload_planilla(file: UploadFile = File(...)):
    pdf_content = await file.read()
    with io.BytesIO(pdf_content) as pdf_stream:
        df = extract_planilla_pila(pdf_stream)

    if df is None or df.empty:
        return JSONResponse(
            status_code=422,
            content={"error": "No se encontraron datos de empleados en el PDF."}
        )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Empleados')
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=planilla_pila.xlsx"}
    )


@app.get("/")
async def main():
    with open("index.html") as f:
        return HTMLResponse(content=f.read(), status_code=200)
