from medical_app.catalog_export import _csv_safe


def test_csv_export_neutralizes_spreadsheet_formulas():
    for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)"):
        assert _csv_safe(value) == f"'{value}"
    assert _csv_safe("普通题干") == "普通题干"
    assert _csv_safe(12) == 12
