import os
import shutil
from datetime import datetime
from app.models import OntRecord, CleanupCandidate, CleanupResult
from app.backup_manager import BackupManager

SAMPLE_CONFIG = """
[ont-configuration]
<ont-configuration>
ont add 0 3 sn-auth "4857544312345678" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "CLIENTE_TESTE"
ont port native-vlan 0 3 eth 1 vlan 100 priority 0
#
[service-port]
<service-port>
service-port 1050 vlan 100 gpon 0/1/0 ont 3 gemport 1 multi-service user-vlan 100 tag-transform translate
service-port 1051 vlan 200 gpon 0/1/0 ont 3 gemport 2 multi-service user-vlan 200 tag-transform translate
#
return
"""


def test_generate_rollback_commands(tmp_path):
    bm = BackupManager(str(tmp_path))
    ont = OntRecord(frame=0, slot=1, port=0, ont_id=3, run_state="offline", sn="4857544312345678")
    cand = CleanupCandidate(
        ont=ont,
        service_ports=[1050, 1051],
        raw_current_config=SAMPLE_CONFIG,
    )

    commands = bm.generate_rollback_commands(cand)
    assert commands[0] == "config"
    assert "interface gpon 0/1" in commands
    assert any("ont add 0 3" in c for c in commands)
    assert any("service-port 1050" in c for c in commands)
    assert any("service-port 1051" in c for c in commands)


def test_save_backup_and_rollback(tmp_path):
    bm = BackupManager(str(tmp_path))
    ont = OntRecord(frame=0, slot=1, port=0, ont_id=3, run_state="offline", sn="4857544312345678")
    cand = CleanupCandidate(
        ont=ont,
        service_ports=[1050, 1051],
        raw_current_config=SAMPLE_CONFIG,
    )

    backup_path = bm.save_candidate_backup(cand)
    assert os.path.exists(backup_path)
    with open(backup_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "4857544312345678" in content
    assert "service-port 1050" in content

    rollback_path = bm.save_rollback_script(cand)
    assert os.path.exists(rollback_path)
    with open(rollback_path, "r", encoding="utf-8") as f:
        r_content = f.read()
    assert "interface gpon 0/1" in r_content
    assert "service-port 1050" in r_content


def test_save_and_parse_deletion_history_txt(tmp_path):
    history_dir = tmp_path / "historico_exclusoes"
    bm = BackupManager(str(tmp_path / "backups"), history_dir=str(history_dir))

    ont1 = OntRecord(
        frame=0,
        slot=1,
        port=2,
        ont_id=5,
        olt_host="10.10.0.1",
        olt_name="OLT-CENTRAL",
        sn="4857544312345678",
        mac="00e0-fc11-2233",
        description="cliente.joao",
        customer_login="cliente.joao",
        run_state="offline",
        last_down_cause="dying-gasp",
        calculated_offline_days=90.0,
    )
    res1 = CleanupResult(
        ont=ont1,
        service_ports_deleted=[1050, 1051],
        ont_deleted=True,
        success=True,
        rollback_commands=[
            "config",
            "interface gpon 0/1",
            'ont add 2 5 sn-auth "4857544312345678" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "cliente.joao"',
            "quit",
            "service-port 1050 vlan 100 gpon 0/1/2 ont 5 gemport 1 multi-service user-vlan 100 tag-transform translate",
        ],
    )

    dt = datetime(2026, 11, 19, 21, 55, 0)
    saved_path = bm.save_deletion_history_txt([res1], execution_time=dt, is_dry_run=False)
    assert saved_path is not None
    assert os.path.basename(saved_path) == "2026-11-19_21-55-00.txt"
    assert os.path.exists(saved_path)

    files = bm.list_deletion_history_files()
    assert len(files) == 1
    assert files[0] == saved_path

    with open(saved_path, "r", encoding="utf-8") as f:
        raw_txt = f.read()

    meta, parsed = BackupManager.parse_deletion_history_txt(raw_txt)
    assert meta["DATA_HORA"] == "2026-11-19 21:55:00"
    assert meta["MODO"] == "REAL"
    assert meta["TOTAL_ONTS"] == "1"
    assert len(parsed) == 1

    p0 = parsed[0]
    assert p0.ont.olt_name == "OLT-CENTRAL"
    assert p0.ont.olt_host == "10.10.0.1"
    assert p0.ont.location == "0/1/2:5"
    assert p0.ont.customer_login == "cliente.joao"
    assert p0.ont.sn == "4857544312345678"
    assert p0.ont.mac == "00e0-fc11-2233"
    assert p0.ont.calculated_offline_days == 90.0
    assert p0.service_ports_deleted == [1050, 1051]
    assert len(p0.rollback_commands) == 5
    assert "interface gpon 0/1" in p0.rollback_commands


