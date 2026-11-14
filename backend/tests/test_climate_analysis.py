from __future__ import annotations

from datetime import date

from app.services import climate_analysis, nasa_power


def test_synthetic_series_flags_itself_as_synthetic() -> None:
    series = nasa_power.synthetic_series(13.34, 77.1, date(2020, 1, 1), date(2020, 12, 31), reason="test")
    public = series.to_public()
    assert public["synthetic"] is True
    assert "synthetic" in public["provenance"].lower()
    assert len(public["days"]) >= 360


def test_analyse_produces_baseline_and_monthly_climatology() -> None:
    series = nasa_power.synthetic_series(13.34, 77.1, date(2016, 1, 1), date(2026, 11, 1), reason="test")
    result = climate_analysis.analyse(series)

    assert result["period"]["days_observed"] > 3000
    assert result["baseline"]["annual_rain_mm"] is not None
    assert len(result["monthly_climatology"]) == 12
    assert len(result["annual_series"]) >= 9
    assert result["data_quality"]["confidence"] in {"high", "medium", "low"}
    assert 0 <= result["stress"]["score"] <= 100
    assert result["vegetation_proxy"]["is_ndvi"] is False


def test_short_series_reports_low_confidence() -> None:
    series = nasa_power.synthetic_series(13.34, 77.1, date(2026, 1, 1), date(2026, 2, 1), reason="test")
    result = climate_analysis.analyse(series)
    assert result["data_quality"]["confidence"] == "low"


def test_window_rain_splits_across_months() -> None:
    monthly = {month: 30.0 for month in range(1, 13)}
    from app.services.rotation_engine import rain_in_window

    start = date(2026, 1, 16)
    end = date(2026, 2, 14)
    value = rain_in_window(monthly, start, end)
    # 16 days of January + 14 days of February, 30 mm per month
    assert 29.0 <= value <= 31.0
