import pandas as pd

from superhealth import database as db
from superhealth.dashboard import data_loader


def _clear_dashboard_caches():
    for fn in (
        data_loader.load_active_conditions,
        data_loader.load_active_condition_metric_mappings,
        data_loader.load_multiple_unified_trends,
        data_loader.load_trendable_metrics_for_active_conditions,
    ):
        fn.clear()


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
