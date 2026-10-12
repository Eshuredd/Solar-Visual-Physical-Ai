from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.asset_topology import topology_validation_report
from app.db import connect, init_db
from app.scada_import import commit_scada_import, preview_scada_csv
from app.topology_import import commit_topology_import, preview_topology_csv


MAPPING = {name: name for name in (
    "site_id", "inverter_id", "timestamp", "ac_power", "dc_power", "irradiance",
    "inverter_temperature", "operating_state", "grid_state", "source_record_id"
)}


def scada_csv(*rows: list[str]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(MAPPING)
    writer.writerows(rows)
    return output.getvalue().encode()


def test_scada_preview_normalizes_timezone_units_nulls_and_quarantines() -> None:
    init_db(reset=True)
    content = scada_csv(
        ["site-001", "site-001-INV-01", "2026-01-15 10:00:00", "4200000", "4400000", "720", "51", "Running", "Connected", "one"],
        ["site-001", "site-001-INV-01", "2026-01-15 10:00:00", "4200000", "4400000", "720", "51", "Running", "Connected", "one"],
        ["site-002", "site-001-INV-01", "bad", "-1", "", "", "", "", "", "bad"],
    )
    with connect() as conn:
        result = preview_scada_csv(conn, site_id="site-001", filename="fictional.csv", content=content,
            mapping=MAPPING, units={"ac_power": "W", "dc_power": "W"}, timezone_name="Asia/Kolkata", source_system="test")
        assert (result["accepted_records"], result["duplicate_records"], result["rejected_records"]) == (1, 1, 1)
        assert result["preview"][0]["timestamp_utc"] == "2026-01-15T04:30:00Z"
        assert result["preview"][0]["ac_power_kw"] == 4200
        assert result["preview"][0]["ac_voltage_v"] is None
        assert "cross-site" in " ".join(result["rejection_preview"][0]["reasons"])


def test_scada_commit_is_idempotent_separated_and_reset_safe() -> None:
    init_db(reset=True)
    content = scada_csv(["site-001", "site-001-INV-01", "2026-01-15T10:00:00Z", "4200", "4400", "720", "51", "Running", "Connected", "one"])
    with connect() as conn:
        preview = preview_scada_csv(conn, site_id="site-001", filename="one.csv", content=content,
            mapping=MAPPING, units={}, timezone_name="UTC", source_system="test")
        committed = commit_scada_import(conn, preview["import_id"])
        repeated = commit_scada_import(conn, preview["import_id"])
        assert repeated["already_imported"] is True
        assert conn.execute("SELECT COUNT(*) FROM imported_inverter_telemetry").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM inverter_telemetry").fetchone()[0] > 1
    client = TestClient(main.app)
    selected = client.get(f"/api/inverters/site-001-INV-01/telemetry?dataset_id={committed['dataset_id']}").json()
    assert selected["count"] == 1 and selected["data_classification"] == "imported_scada"
    assert selected["threshold_calibration_status"] == "uncalibrated_for_imported_site"
    assert client.post("/api/reset?demo_mode=true").status_code == 409


def test_commit_rolls_back_all_rows_on_staging_failure() -> None:
    init_db(reset=True)
    content = scada_csv(["site-001", "site-001-INV-01", "2026-01-15T10:00:00Z", "4200", "4400", "720", "51", "Running", "Connected", "one"])
    with connect() as conn:
        preview = preview_scada_csv(conn, site_id="site-001", filename="rollback.csv", content=content,
            mapping=MAPPING, units={}, timezone_name="UTC", source_system="test")
        staged = json.loads(conn.execute("SELECT normalized_json FROM data_import_staging WHERE import_id=?", (preview["import_id"],)).fetchone()[0])
        staged.pop("ac_power_kw")
        conn.execute("UPDATE data_import_staging SET normalized_json=? WHERE import_id=?", (json.dumps(staged),preview["import_id"])); conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM telemetry_datasets").fetchone()[0]
        with pytest.raises(KeyError): commit_scada_import(conn, preview["import_id"])
        assert conn.execute("SELECT COUNT(*) FROM telemetry_datasets").fetchone()[0] == before
        assert conn.execute("SELECT status FROM data_imports WHERE import_id=?", (preview["import_id"],)).fetchone()[0] == "previewed"


def _topology_csv() -> bytes:
    fields = ["record_type","site_id","block_code","display_name","expected_row_count","row_number","module_position_count","inverter_id","mppt_id","input_index","string_id","string_index","asset_group_id","module_start","module_end"]
    rows = [
        {"record_type":"block","site_id":"site-002","block_code":"X1","display_name":"Fictional block","expected_row_count":"1"},
        {"record_type":"row","site_id":"site-002","block_code":"X1","row_number":"1","module_position_count":"34"},
        {"record_type":"mppt","site_id":"site-002","inverter_id":"site-002-INV-01","mppt_id":"X-M1","input_index":"1"},
        {"record_type":"string","site_id":"site-002","mppt_id":"X-M1","string_id":"X-S1","string_index":"1"},
        {"record_type":"asset_group","site_id":"site-002","block_code":"X1","row_number":"1","asset_group_id":"X-G1","module_start":"1","module_end":"17"},
        {"record_type":"string_asset_group","site_id":"site-002","string_id":"X-S1","asset_group_id":"X-G1"},
    ]
    output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return output.getvalue().encode()


def test_topology_import_supports_variable_rows_and_derives_partial_coverage() -> None:
    init_db(reset=True)
    with connect() as conn:
        conn.execute("INSERT INTO inverters VALUES ('site-002-INV-01','site-002','Fictional INV',100,'Example','FIC','Operational',NULL,'imported')"); conn.commit()
        preview = preview_topology_csv(conn, site_id="site-002", filename="topology.csv", content=_topology_csv(),
            source_system="fictional engineering export", mapping_version="v1", effective_date="2026-01-01")
        assert preview["rejected_records"] == 0
        result = commit_topology_import(conn, preview["import_id"])
        assert result["validation"]["declared_module_positions"] == 34
        assert result["validation"]["mapped_module_positions"] == 17
        assert result["validation"]["verification_status"] == "partially_mapped"
        with pytest.raises(Exception, match="overlapping"):
            conn.execute("""INSERT INTO physical_asset_groups
              (asset_group_id,site_id,block_code,display_name,start_ordinal,end_ordinal,row_start,row_end,module_start,module_end,mapped_module_count,mapping_classification,provenance,row_number)
              VALUES ('overlap','site-002','X1','Overlap',20,30,1,1,10,20,11,'unverified','test',1)""")


def test_import_endpoints_require_guard_and_are_site_scoped() -> None:
    init_db(reset=True); client=TestClient(main.app); content=scada_csv(["site-001","site-001-INV-01","2026-01-15T10:00:00Z","1","2","500","30","Running","Connected","one"])
    files={"file":("x.csv",content,"text/csv")}; data={"mapping_json":json.dumps(MAPPING),"units_json":"{}","demo_mode":"true"}
    assert client.post("/api/sites/site-001/data-imports/scada/preview",files=files,data=data).status_code == 403
    preview=client.post("/api/sites/site-001/data-imports/scada/preview",files={"file":("x.csv",content,"text/csv")},data=data,headers={"X-DeepDrishti-Demo-Operator":"acknowledged"}).json()
    assert client.get(f"/api/sites/site-002/data-imports/{preview['import_id']}").status_code == 404
