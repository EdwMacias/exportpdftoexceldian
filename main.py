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
        # Dot is decimal separator; commas are thousands separators → remove commas
        s = s.replace(',', '')
    elif last_comma > last_dot:
        # Comma is decimal separator; dots are thousands separators → remove dots, comma→dot
        s = s.replace('.', '').replace(',', '.')
    # else: no separator at all → plain integer string, keep as-is

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


@app.get("/")
async def main():
    with open("index.html") as f:
        return HTMLResponse(content=f.read(), status_code=200)
