import pandas as pd

from superhealth import database as db
from superhealth.dashboard import data_loader
from superhealth.dashboard.components.charts import (
    chart_medical_timeline,
    chart_unified_lab_trend,
)
from superhealth.dashboard.views.lab_results import _add_multi_metric_subplot_traces


def _clear_dashboard_caches():
    for fn in (
        data_loader.load_active_conditions,
        data_loader.load_active_condition_metric_mappings,
        data_loader.load_eye_exams,
        data_loader.load_multiple_unified_trends,
        data_loader.load_trendable_metrics_for_active_conditions,
    ):
        fn.clear()


def test_checkup_pivot_does_not_map_hemoglobin_to_hba1c():
    row = data_loader._pivot_observations_to_row(
        [
            {
                "item_name": "血红蛋白",
                "item_code": "Hb",
                "value_num": 151.0,
            }
        ],
        data_loader._CHECKUP_ITEM_MAP,
    )

    assert row["hgb"] == 151.0
    assert "hba1c" not in row


def test_checkup_pivot_maps_actual_hba1c():
    row = data_loader._pivot_observations_to_row(
        [{"item_name": "糖化血红蛋白", "value_num": 5.4}],
        data_loader._CHECKUP_ITEM_MAP,
    )

    assert row["hba1c"] == 5.4


def test_checkup_pivot_maps_fasting_blood_glucose_alias():
    row = data_loader._pivot_observations_to_row(
        [{"item_name": "空腹血葡萄糖", "item_code": "FBG", "value_num": 5.38}],
        data_loader._CHECKUP_ITEM_MAP,
    )

    assert row["fasting_glucose"] == 5.38


def test_trendable_metrics_require_active_condition_mapping_and_numeric_data(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "dashboard.db"
    db.init_db(db_path)
    monkeypatch.setattr(data_loader, "DEFAULT_DB_PATH", db_path)
    _clear_dashboard_caches()

    with db.get_conn(db_path) as conn:
        db.upsert_medical_condition(conn, name="高尿酸血症", status="active")
        db.upsert_medical_condition(conn, name="青光眼", status="resolved")
        db.upsert_condition_metric_mapping(
            conn, condition_name="高尿酸血症", metric_key="uric_acid"
        )
        db.upsert_condition_metric_mapping(conn, condition_name="青光眼", metric_key="iop")
        db.bulk_insert_observations(
            conn,
            [
                {
                    "obs_date": "2025-01-01",
                    "category": "lab",
                    "item_name": "尿酸",
                    "value_num": 420,
                },
                {
                    "obs_date": "2025-01-01",
                    "category": "eye",
                    "item_name": "右眼眼压",
                    "value_num": 17,
                },
            ],
        )

    result = data_loader.load_trendable_metrics_for_active_conditions(10)

    assert list(result.keys()) == ["uric_acid"]
    assert isinstance(result["uric_acid"], pd.DataFrame)
    assert result["uric_acid"].iloc[0]["value"] == 420


def test_trendable_metrics_skip_text_only_observations(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    db.init_db(db_path)
    monkeypatch.setattr(data_loader, "DEFAULT_DB_PATH", db_path)
    _clear_dashboard_caches()

    with db.get_conn(db_path) as conn:
        db.upsert_medical_condition(conn, name="甲状腺结节", status="active")
        db.upsert_condition_metric_mapping(conn, condition_name="甲状腺结节", metric_key="tsh")
        db.bulk_insert_observations(
            conn,
            [
                {
                    "obs_date": "2025-01-01",
                    "category": "lab",
                    "item_name": "促甲状腺激素",
                    "value_text": "正常",
                }
            ],
        )

    assert data_loader.load_trendable_metrics_for_active_conditions(10) == {}


def test_uric_acid_trend_excludes_urine_ph(tmp_path):
    db_path = tmp_path / "dashboard.db"
    db.init_db(db_path)

    with db.get_conn(db_path) as conn:
        db.bulk_insert_observations(
            conn,
            [
                {
                    "obs_date": "2025-01-01",
                    "category": "lab",
                    "item_name": "尿酸碱度",
                    "item_code": "PH",
                    "value_num": 6.0,
                },
                {
                    "obs_date": "2025-01-01",
                    "category": "lab",
                    "item_name": "尿酸",
                    "item_code": "UA",
                    "value_num": 463.0,
                },
            ],
        )

        rows = db.query_lab_trends_unified(conn, "uric_acid", start_date="2024-01-01")

    assert [row["value"] for row in rows] == [463.0]


def test_unified_lab_trend_uses_single_chronological_line_across_sources():
    df = pd.DataFrame(
        [
            {"date": "2024-06-01", "value": 420, "source": "lab"},
            {"date": "2025-01-01", "value": 460, "source": "annual_checkup"},
            {"date": "2024-01-01", "value": 500, "source": "annual_checkup"},
        ]
    )
    df["date"] = pd.to_datetime(df["date"])

    fig = chart_unified_lab_trend(df, title="尿酸", unit="μmol/L")

    assert fig.data[0].name == "趋势"
    assert list(fig.data[0].x) == list(
        pd.to_datetime(["2024-01-01", "2024-06-01", "2025-01-01"])
    )
    assert list(fig.data[0].y) == [500, 420, 460]
    assert all(trace.mode == "markers" for trace in fig.data[1:])


def test_multi_metric_subplot_uses_single_chronological_line_across_sources():
    from plotly.subplots import make_subplots

    df = pd.DataFrame(
        [
            {"date": "2021-09-20", "value": 14, "source": "annual_checkup"},
            {"date": "2021-09-20", "value": 13, "source": "annual_checkup"},
            {"date": "2024-05-22", "value": 16, "source": "outpatient"},
            {"date": "2024-05-22", "value": 15, "source": "outpatient"},
        ]
    )
    df["date"] = pd.to_datetime(df["date"])
    fig = make_subplots(rows=1, cols=1)

    _add_multi_metric_subplot_traces(fig, df, "iop", 1)

    assert fig.data[0].name == "眼压 (IOP) - 趋势"
    assert fig.data[0].mode == "lines"
    assert list(fig.data[0].x) == list(
        pd.to_datetime(
            ["2021-09-20", "2021-09-20", "2024-05-22", "2024-05-22"]
        )
    )
    assert list(fig.data[0].y) == [14, 13, 16, 15]
    assert [trace.mode for trace in fig.data[1:]] == ["markers", "markers"]


def test_medical_timeline_does_not_include_eye_followup_by_default():
    checkups = pd.DataFrame()
    eye_exams = pd.DataFrame([{"date": "2025-01-01"}])
    labs = pd.DataFrame([{"date": "2025-02-01", "source": "lab"}])

    fig = chart_medical_timeline(checkups, eye_exams, labs)

    assert "化验复查" in list(fig.data[0].y)
    assert "眼科随访" not in list(fig.data[0].y)


def test_medical_timeline_has_no_lab_followup_without_lab_rows():
    fig = chart_medical_timeline(
        pd.DataFrame([{"checkup_date": "2025-01-01"}]),
        pd.DataFrame(),
        pd.DataFrame(columns=["date", "source"]),
    )

    assert "年度体检" in list(fig.data[0].y)
    assert "化验复查" not in list(fig.data[0].y)


def test_medical_timeline_guards_event_source_types():
    fig = chart_medical_timeline(
        pd.DataFrame([{"checkup_date": "2021-01-01"}]),
        pd.DataFrame(
            [
                {"date": "2021-01-01", "source": "annual_checkup"},
                {"date": "2025-01-01", "source": "outpatient"},
            ]
        ),
        pd.DataFrame(
            [
                {"date": "2021-01-01", "source": "annual_checkup"},
                {"date": "2025-02-01", "source": "lab"},
            ]
        ),
        include_eye_exams=True,
    )

    timeline_dates = {
        (trace.name, pd.to_datetime(trace.base[0]).strftime("%Y-%m-%d"))
        for trace in fig.data
    }

    assert ("眼科", "2021-01-01") not in timeline_dates
    assert ("眼科", "2025-01-01") in timeline_dates
    assert ("化验", "2021-01-01") not in timeline_dates
    assert ("化验", "2025-02-01") in timeline_dates


def test_lab_results_timeline_filters_non_lab_observations():
    from superhealth.dashboard.views.lab_results import _timeline_lab_rows

    df_lab = pd.DataFrame(
        [
            {"date": "2025-01-01", "source": "outpatient", "item_name": "眼压"},
            {"date": "2025-02-01", "source": "annual_checkup", "item_name": "尿酸"},
            {"date": "2025-03-01", "source": "lab", "item_name": "尿酸"},
        ]
    )

    timeline_lab = _timeline_lab_rows(df_lab, {"尿酸", "眼压"})

    assert list(timeline_lab["date"]) == ["2025-03-01"]


def test_eye_exams_exclude_annual_checkup_eye_items(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    db.init_db(db_path)
    monkeypatch.setattr(data_loader, "DEFAULT_DB_PATH", db_path)
    _clear_dashboard_caches()

    with db.get_conn(db_path) as conn:
        checkup_doc = db.insert_medical_document(
            conn,
            doc_date="2021-01-01",
            doc_type="annual_checkup",
            markdown_path="annual.md",
        )
        eye_doc = db.insert_medical_document(
            conn,
            doc_date="2025-01-01",
            doc_type="outpatient",
            markdown_path="eye.md",
            department="眼科",
        )
        db.bulk_insert_observations(
            conn,
            [
                {
                    "document_id": checkup_doc,
                    "obs_date": "2021-01-01",
                    "category": "eye",
                    "item_name": "右眼眼压",
                    "value_num": 16,
                },
                {
                    "document_id": eye_doc,
                    "obs_date": "2025-01-01",
                    "category": "eye",
                    "item_name": "右眼眼压",
                    "value_num": 18,
                },
            ],
        )

    df_eye = data_loader.load_eye_exams()

    assert list(df_eye["date"].dt.strftime("%Y-%m-%d")) == ["2025-01-01"]
    assert list(df_eye["source"]) == ["outpatient"]


def test_lab_results_timeline_filters_annual_eye_followups():
    from superhealth.dashboard.views.lab_results import _timeline_eye_rows

    df_eye = pd.DataFrame(
        [
            {"date": "2021-01-01", "source": "annual_checkup"},
            {"date": "2025-01-01", "source": "outpatient"},
        ]
    )

    timeline_eye = _timeline_eye_rows(df_eye)

    assert list(timeline_eye["date"]) == ["2025-01-01"]
