from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
import pdfplumber
import pandas as pd
import io

app = FastAPI()

def extract_data_from_pdf(pdf_stream):
    import re
    vendedor = {}
    comprador = {}
    items = []

    with pdfplumber.open(pdf_stream) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text() + "\n"

        # Extract Vendedor data
        vendedor_razon_social = re.search(r"Razón Social: (.*?)\n", full_text)
        vendedor_nit = re.search(r"Nit del Emisor: (.*?)\n", full_text)
        if vendedor_razon_social and vendedor_nit:
            vendedor = {
                "Razón Social": vendedor_razon_social.group(1).strip(),
                "NIT": vendedor_nit.group(1).strip(),
            }

        # Extract Comprador data
        comprador_razon_social = re.search(r"Datos del Adquiriente / Comprador\nNombre o Razón Social: (.*?)\n", full_text)
        comprador_nit = re.search(r"Número Documento: (.*?)\n", full_text)
        if comprador_razon_social and comprador_nit:
            comprador = {
                "Razón Social": comprador_razon_social.group(1).strip(),
                "NIT": comprador_nit.group(1).strip(),
            }

        # Extract Items from tables
        # Let's assume the main items table is on the first page for this model
        page = pdf.pages[0]
        tables = page.extract_tables()
        if tables:
            # The first table seems to be the one with the items
            item_table = tables[0]
            # Headers are split in two rows, data starts from the 3rd row (index 2)
            headers = ["Nro", "Código", "Descripción", "U/M", "Cantidad", "Precio unitario", "Descuento", "Recargo", "IVA", "IVA %", "INC", "INC %", "Total Item"]
            for row in item_table[2:]:
                item_data = {}
                # Ensure row has enough columns to avoid IndexError
                for i, header in enumerate(headers):
                    if i < len(row):
                        item_data[header] = row[i]
                    else:
                        item_data[header] = None
                items.append(item_data)

    # Prepare data for Excel sheets
    df_info = pd.DataFrame({
        'Tipo': ['Vendedor', 'Comprador'],
        'Razón Social': [vendedor.get('Razón Social'), comprador.get('Razón Social')],
        'NIT': [vendedor.get('NIT'), comprador.get('NIT')]
    })

    df_items = pd.DataFrame(items)

    return {"Info": df_info, "Items": df_items}

@app.post("/uploadfile/")
async def create_upload_file(file: UploadFile = File(...)):
    # Read PDF content
    pdf_content = await file.read()

    # In-memory file-like object
    with io.BytesIO(pdf_content) as pdf_stream:
        # Extract data from PDF
        data_frames = extract_data_from_pdf(pdf_stream)

    # Create an Excel file in memory with multiple sheets
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if "Info" in data_frames:
            data_frames["Info"].to_excel(writer, index=False, sheet_name='Info')
        if "Items" in data_frames:
            data_frames["Items"].to_excel(writer, index=False, sheet_name='Items')
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=factura_export.xlsx"}
    )

@app.get("/")
async def main():
    with open("index.html") as f:
        return HTMLResponse(content=f.read(), status_code=200)

