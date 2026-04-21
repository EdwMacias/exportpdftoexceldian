# Extractor de Documentos Colombianos

FastAPI web app que extrae datos de PDFs colombianos y los exporta a Excel.

## Extractores disponibles

| Extractor | Entrada | Salida |
|---|---|---|
| **Extracto Bancario** | PDF de extracto bancario | `.xlsx` con columnas `Entradas` / `Salidas` |
| **Factura DIAN** | PDF de factura electrónica DIAN | `.xlsx` con hojas `Info` (vendedor/comprador) e `Items` |
| **Planilla PILA** | PDF de planilla PILA | `.xlsx` con aportes por empleado |

### Bancos soportados (Extracto Bancario)

- Bancolombia
- Davivienda
- BBVA
- Scotiabank Colpatria
- Adquirencia / datáfonos (BBVA)
- Otros bancos con tablas estructuradas en PDF

## Correr en local

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

App disponible en `http://localhost:8000`.

## Correr con Docker

```bash
docker-compose up --build
```

App disponible en `http://localhost:3002`.

## Producción (Docker + nginx + HTTPS)

```bash
# 1. Editar nginx/nginx.conf — reemplazar YOUR_DOMAIN con el dominio real
# 2. Obtener certificado SSL:
certbot certonly --standalone -d tu-dominio.com
# 3. Desplegar:
docker-compose -f docker-compose.prod.yml up --build -d
```

- `docker-compose.yml` — local (puerto 3002 expuesto directamente)
- `docker-compose.prod.yml` — producción (nginx en 80/443, app no expuesta)

## Tests

```bash
python -m pytest test_extractors.py -v
```

## Depurar PDFs

```bash
python inspect_pdf.py "ruta/al/archivo.pdf"
```

Imprime texto y tablas de la página 1 — útil para diagnosticar cuando un formato nuevo no es reconocido.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Frontend HTML |
| `POST` | `/upload-bank/` | Extracto bancario PDF → Excel |
| `POST` | `/uploadfile/` | Factura DIAN PDF → Excel |
| `POST` | `/upload-planilla/` | Planilla PILA PDF → Excel |

## Stack

- Python 3.9
- FastAPI
- pdfplumber
- pandas / openpyxl
- Docker (`python:3.9-slim`)
