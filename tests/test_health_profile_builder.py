from __future__ import annotations

import pytest

from superhealth import database as db
from superhealth.core.health_profile_builder import HealthProfile, HealthProfileBuilder


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def test_load_genetic_markers_from_structured_observations(tmp_db):
    with db.get_conn(tmp_db) as conn:
        doc_id = db.insert_medical_document(
            conn,
            doc_date="2024-07-04",
            doc_type="genetic",
            title="全面肿瘤18项基因检测报告",
            markdown_path="data/genetic-data/2024-07-04-全面肿瘤18项基因检测报告.md",
        )
        db.bulk_insert_observations(
            conn,
            [
                {
                    "document_id": doc_id,
                    "obs_date": "2024-07-04",
                    "category": "genetic",
                    "item_name": "肾癌",
                    "value_text": "风险较高",
                    "is_abnormal": 1,
                    "note": "VDR rs1544410 GG 18.0%; CYP1A1 rs1048943 TT 91.0%",
                },
                {
                    "document_id": doc_id,
                    "obs_date": "2024-07-04",
                    "category": "genetic",
                    "item_name": "急性淋巴细胞白血病-ALL",
                    "value_text": "风险较高",
                    "is_abnormal": 1,
                    "note": "MTHFR rs1801133 Null 64.0%; GSTM1 Null/Present 58.0%",
                },
                {
                    "document_id": doc_id,
                    "obs_date": "2024-07-04",
                    "category": "genetic",
                    "item_name": "慢性淋巴细胞白血病-CLL",
                    "value_text": "风险正常",
                    "is_abnormal": 0,
                    "note": "XRCC1 rs25487 AA 55.0%",
                },
            ],
        )

    profile = HealthProfile()
    HealthProfileBuilder(tmp_db)._load_genetic_markers(profile)

    assert {"VDR", "CYP1A1", "MTHFR", "GSTM1"}.issubset(profile.genetic_markers)
    assert "CLL" not in profile.genetic_markers
    assert "GG" not in profile.genetic_markers
    assert "TT" not in profile.genetic_markers
    assert profile.profile_sources[0]["category"] == "基因特征"
    assert "肾癌" in profile.profile_sources[0]["finding"]
    assert "急性淋巴细胞白血病-ALL" in profile.profile_sources[0]["finding"]
    assert "慢性淋巴细胞白血病-CLL" not in profile.profile_sources[0]["finding"]
