import os
import shutil
from app.models import OntRecord, CleanupCandidate
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

