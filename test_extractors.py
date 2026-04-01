"""Tests for PDF extractors: clean_number, BBVA, and Adquirencia."""
import io
import pytest
from main import (
    clean_number,
    extract_bank_statement,
    _parse_bbva_text,
    _parse_adquirencia_tables,
    _is_adquirencia_table,
    _BBVA_PATTERN,
    _BBVA_OPENING_BALANCE,
)

BBVA_PDF = "pdf/extracto cuenta cte BBVA Feb 2026.pdf"
ADQUIRENCIA_PDF = "pdf/Extracto datáfonos Adquirencia Feb 2026.pdf"


# ── clean_number ──────────────────────────────────────────────────────────────

class TestCleanNumber:
    def test_multiple_dots_thousands(self):
        assert clean_number("$ 1.225.000") == 1225000

    def test_dot_thousands_comma_decimal(self):
        assert clean_number("$ 336.050,42") == 336050.42

    def test_comma_thousands_three_digits(self):
        assert clean_number("$212,700") == 212700

    def test_us_format_dot_decimal(self):
        assert clean_number("24,300.00") == 24300

    def test_us_large_amount(self):
        assert clean_number("2,545,068.95") == 2545068.95

    def test_round_us_amount(self):
        assert clean_number("43,885.00") == 43885

    def test_plain_integer(self):
        assert clean_number("1234") == 1234

    def test_none_returns_none(self):
        assert clean_number(None) is None

    def test_empty_string_returns_none(self):
        assert clean_number("") is None

    def test_dash_returns_none(self):
        assert clean_number("-") is None

    def test_zero(self):
        assert clean_number("0") == 0

    def test_colombian_no_decimals(self):
        # "35,850" — comma with 3 trailing digits → thousands separator
        assert clean_number("35,850") == 35850


# ── BBVA regex pattern ────────────────────────────────────────────────────────

class TestBBVAPattern:
    def test_matches_deposit_line(self):
        line = "23204 02-02-2026 31-01-2026 DEP. ELECTRONICO COMERCIO 0016870008 2,545,068.95 61,659,540.48"
        m = _BBVA_PATTERN.match(line)
        assert m is not None
        fecha, desc, amount, saldo = m.groups()
        assert fecha == "02-02-2026"
        assert "DEP. ELECTRONICO COMERCIO 0016870008" in desc
        assert amount == "2,545,068.95"
        assert saldo == "61,659,540.48"

    def test_matches_abono_line(self):
        line = "23205 31-01-2026 02-02-2026 ABONO CON BRE-B ENLACE DE APLICATIVO 43,885.00 61,703,425.48"
        m = _BBVA_PATTERN.match(line)
        assert m is not None
        _, _, amount, _ = m.groups()
        assert amount == "43,885.00"

    def test_matches_cargo_line(self):
        line = "23224 31-01-2026 02-02-2026 ND CARGO AGRUPADO NOMINAS NETCASH IBAGUE 3,669,856.00 58,033,569.48"
        m = _BBVA_PATTERN.match(line)
        assert m is not None

    def test_does_not_match_header_line(self):
        line = "PERÍODO DESDE: 01-02-2026 HASTA: 28-02-2026"
        assert _BBVA_PATTERN.match(line) is None

    def test_does_not_match_summary_line(self):
        line = "SALDO CIERRE MES ANTERIOR 59,114,471.53 - IVA 9 6,061.00"
        assert _BBVA_PATTERN.match(line) is None

    def test_opening_balance_pattern(self):
        text = "SALDO CIERRE MES ANTERIOR 59,114,471.53 - IVA 9 6,061.00"
        m = _BBVA_OPENING_BALANCE.search(text)
        assert m is not None
        assert m.group(1) == "59,114,471.53"


# ── BBVA text parser ──────────────────────────────────────────────────────────

class TestParseBBVAText:
    # Three transactions: two entradas, one salida.
    # Balances computed from opening 59,114,471.53:
    #   +2,545,068.95 → 61,659,540.48  (entrada)
    #   +43,885.00    → 61,703,425.48  (entrada)
    #   -3,669,856.00 → 58,033,569.48  (salida)
    SAMPLE = (
        "SALDO CIERRE MES ANTERIOR 59,114,471.53\n"
        "23204 02-02-2026 31-01-2026 DEP. ELECTRONICO COMERCIO 0016870008 2,545,068.95 61,659,540.48\n"
        "23205 31-01-2026 02-02-2026 ABONO CON BRE-B ENLACE DE APLICATIVO 43,885.00 61,703,425.48\n"
        "23224 31-01-2026 02-02-2026 ND CARGO AGRUPADO NOMINAS NETCASH IBAGUE 3,669,856.00 58,033,569.48\n"
    )

    def test_returns_non_empty_dataframe(self):
        df = _parse_bbva_text(self.SAMPLE)
        assert df is not None and not df.empty

    def test_row_count(self):
        df = _parse_bbva_text(self.SAMPLE)
        assert len(df) == 3

    def test_has_required_columns(self):
        df = _parse_bbva_text(self.SAMPLE)
        for col in ("Fecha", "Descripción", "Entradas", "Salidas", "Saldo"):
            assert col in df.columns

    def test_first_row_is_entrada(self):
        import pandas as pd
        df = _parse_bbva_text(self.SAMPLE)
        row = df.iloc[0]
        assert row["Entradas"] == pytest.approx(2545068.95)
        assert pd.isna(row["Salidas"])

    def test_second_row_is_entrada(self):
        import pandas as pd
        df = _parse_bbva_text(self.SAMPLE)
        row = df.iloc[1]
        assert row["Entradas"] == 43885
        assert pd.isna(row["Salidas"])

    def test_third_row_is_salida(self):
        import pandas as pd
        df = _parse_bbva_text(self.SAMPLE)
        row = df.iloc[2]
        assert row["Salidas"] == pytest.approx(3669856.0)
        assert pd.isna(row["Entradas"])

    def test_fecha_format(self):
        df = _parse_bbva_text(self.SAMPLE)
        assert df.iloc[0]["Fecha"] == "02-02-2026"

    def test_no_opening_balance_still_parses(self):
        text_no_ob = (
            "23204 02-02-2026 31-01-2026 DEP. ELECTRONICO 0016870008 2,545,068.95 61,659,540.48\n"
        )
        df = _parse_bbva_text(text_no_ob)
        assert df is not None and len(df) == 1
        # Without opening balance, defaults to Entrada
        assert df.iloc[0]["Entradas"] is not None


# ── Adquirencia table detection ───────────────────────────────────────────────

class TestIsAdquirenciaTable:
    PAGE1_TABLE = [
        ["Detalle de Movimientos", None, None, None, None, None, None, None, None, None, None, None, None],
        ["Fecha\nde\nAbono", "Fecha\nde\nOperaci\nón", "FR (1)", "Num\nAutor", "Cod. Estab.",
         "Compras:", "Iva:", "Propina:", "Comisión:", "ReteIva:", "Retefte", "ReteIca:", "Vr Abono"],
        ["01/02/26", "31/01/26", "VS", "813803", "16870008", "35,850", "0", "0", "520", "0", "538", "72", "35,330"],
    ]
    PAGE2_TABLE = [
        ["Fecha\nde\nAbono", "Fecha\nde\nOperaci\nón", "FR (1)", "Num\nAutor", "Cod. Estab.",
         "Compras:", "Iva:", "Propina:", "Comisión:", "ReteIva:", "Retefte", "ReteIca:", "Vr Abono"],
        ["02/02/26", "01/02/26", "MC", "123456", "16870008", "50,000", "0", "0", "725", "0", "750", "100", "49,275"],
    ]
    SUMMARY_TABLE = [
        ["Información Abonos", None],
        ["Compras:", "72,663,903"],
        ["Iva:", "0"],
    ]

    def test_detects_page1_format(self):
        assert _is_adquirencia_table(self.PAGE1_TABLE) is True

    def test_detects_page2_format(self):
        assert _is_adquirencia_table(self.PAGE2_TABLE) is True

    def test_rejects_summary_table(self):
        assert _is_adquirencia_table(self.SUMMARY_TABLE) is False

    def test_rejects_empty(self):
        assert _is_adquirencia_table([]) is False

    def test_rejects_single_row(self):
        assert _is_adquirencia_table([["Detalle de Movimientos"]]) is False


# ── Adquirencia table parser ──────────────────────────────────────────────────

class TestParseAdquirenciaTables:
    PAGE1_TABLE = [
        ["Detalle de Movimientos", None, None, None, None, None, None, None, None, None, None, None, None],
        ["Fecha\nde\nAbono", "Fecha\nde\nOperaci\nón", "FR (1)", "Num\nAutor", "Cod. Estab.",
         "Compras:", "Iva:", "Propina:", "Comisión:", "ReteIva:", "Retefte", "ReteIca:", "Vr Abono"],
        ["01/02/26", "31/01/26", "VS", "813803", "16870008", "35,850", "0", "0", "520", "0", "538", "72", "35,330"],
        ["01/02/26", "31/01/26", "VS", "139926", "16870008", "44,600", "0", "0", "647", "0", "669", "89", "43,953"],
    ]
    PAGE2_TABLE = [
        ["Fecha\nde\nAbono", "Fecha\nde\nOperaci\nón", "FR (1)", "Num\nAutor", "Cod. Estab.",
         "Compras:", "Iva:", "Propina:", "Comisión:", "ReteIva:", "Retefte", "ReteIca:", "Vr Abono"],
        ["02/02/26", "01/02/26", "MC", "123456", "16870008", "50,000", "0", "0", "725", "0", "750", "100", "49,275"],
    ]

    def test_returns_non_empty_dataframe(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE, self.PAGE2_TABLE])
        assert df is not None and not df.empty

    def test_total_row_count(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE, self.PAGE2_TABLE])
        assert len(df) == 3  # 2 from page1 + 1 from page2

    def test_entradas_is_vr_abono(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE])
        assert df.iloc[0]["Entradas"] == 35330

    def test_compras_column_present(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE])
        assert "Compras" in df.columns
        assert df.iloc[0]["Compras"] == 35850

    def test_comision_column_present(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE])
        assert "Comisión" in df.columns
        assert df.iloc[0]["Comisión"] == 520

    def test_salidas_is_none(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE])
        assert df["Salidas"].isna().all()

    def test_descripcion_contains_fr_and_num_autor(self):
        df = _parse_adquirencia_tables([self.PAGE1_TABLE])
        assert "VS" in df.iloc[0]["Descripción"]
        assert "813803" in df.iloc[0]["Descripción"]

    def test_skips_non_adquirencia_tables(self):
        other_table = [["Información Abonos", None], ["Compras:", "72,663,903"]]
        df = _parse_adquirencia_tables([other_table, self.PAGE1_TABLE])
        assert len(df) == 2  # only PAGE1_TABLE rows counted

    def test_returns_none_for_no_matching_tables(self):
        result = _parse_adquirencia_tables([[["foo", "bar"], ["a", "b"]]])
        assert result is None


# ── Integration: BBVA PDF ─────────────────────────────────────────────────────

class TestBBVAIntegration:
    @pytest.fixture(scope="class")
    def df(self):
        with open(BBVA_PDF, "rb") as f:
            return extract_bank_statement(io.BytesIO(f.read()))

    def test_returns_non_empty_dataframe(self, df):
        assert df is not None and not df.empty

    def test_has_entradas_column(self, df):
        assert "Entradas" in df.columns

    def test_has_salidas_column(self, df):
        assert "Salidas" in df.columns

    def test_transaction_count(self, df):
        # Statement header: +ABONOS 398, -CARGOS 50 → 448 rows total
        assert len(df) >= 400

    def test_entradas_are_positive(self, df):
        entradas = df["Entradas"].dropna()
        assert not entradas.empty
        assert (entradas > 0).all()

    def test_salidas_are_positive(self, df):
        salidas = df["Salidas"].dropna()
        assert not salidas.empty
        assert (salidas > 0).all()

    def test_both_directions_present(self, df):
        assert df["Entradas"].notna().sum() > 0
        assert df["Salidas"].notna().sum() > 0

    def test_fecha_format(self, df):
        # BBVA uses DD-MM-YYYY
        import re
        assert df["Fecha"].str.match(r"\d{2}-\d{2}-\d{4}").all()


# ── Integration: Adquirencia PDF ─────────────────────────────────────────────

class TestAdquirenciaIntegration:
    @pytest.fixture(scope="class")
    def df(self):
        with open(ADQUIRENCIA_PDF, "rb") as f:
            return extract_bank_statement(io.BytesIO(f.read()))

    def test_returns_non_empty_dataframe(self, df):
        assert df is not None and not df.empty

    def test_has_entradas_column(self, df):
        assert "Entradas" in df.columns

    def test_has_compras_column(self, df):
        assert "Compras" in df.columns

    def test_transaction_count(self, df):
        # 27 pages with ~60 transactions each
        assert len(df) > 500

    def test_entradas_positive(self, df):
        entradas = df["Entradas"].dropna()
        assert not entradas.empty
        assert (entradas > 0).all()

    def test_compras_gte_entradas(self, df):
        # Compras >= Vr Abono (after deducting commission)
        valid = df[df["Entradas"].notna() & df["Compras"].notna()]
        assert (valid["Compras"] >= valid["Entradas"]).all()
