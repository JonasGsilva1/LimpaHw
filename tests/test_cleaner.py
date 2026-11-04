import pytest
from datetime import datetime
from unittest.mock import MagicMock
from app.models import OntRecord, CleanupCandidate, OltTarget
from app.config import CleanupSettings
from app.cleaner import OntCleaner, MultiOltCoordinator
from app.backup_manager import BackupManager


def test_evaluate_eligibility_online():
    cleaner = OntCleaner(client=None, backup_manager=None, settings=CleanupSettings(days_offline_threshold=30.0))
    ont = OntRecord(frame=0, slot=1, port=0, ont_id=1, run_state="online")
    eligible, reason = cleaner.evaluate_eligibility(ont)
    assert not eligible
    assert "online" in reason.lower()


def test_evaluate_eligibility_whitelist():
    cleaner = OntCleaner(
        client=None,
        backup_manager=None,
        settings=CleanupSettings(
            days_offline_threshold=30.0,
            description_whitelist=["VIP", "DEDICADO"],
        ),
    )
    ont = OntRecord(
        frame=0,
        slot=1,
        port=0,
        ont_id=1,
        run_state="offline",
        description="CLIENTE_VIP_FIBRA",
        calculated_offline_days=60.0,
    )
    eligible, reason = cleaner.evaluate_eligibility(ont)
    assert not eligible
    assert "whitelist" in reason.lower()


def test_evaluate_eligibility_days_threshold():
    cleaner = OntCleaner(
        client=None,
        backup_manager=None,
        settings=CleanupSettings(days_offline_threshold=30.0),
    )
    # Less than 30 days
    ont_recent = OntRecord(
        frame=0,
        slot=1,
        port=0,
        ont_id=1,
        run_state="offline",
        calculated_offline_days=15.0,
    )
    eligible, reason = cleaner.evaluate_eligibility(ont_recent)
    assert not eligible
    assert "inferior" in reason.lower()

    # Greater than 30 days
    ont_old = OntRecord(
        frame=0,
        slot=1,
        port=0,
        ont_id=2,
        run_state="offline",
        calculated_offline_days=45.0,
    )
    eligible, reason = cleaner.evaluate_eligibility(ont_old)
    assert eligible
    assert "45 dias" in reason


def test_evaluate_eligibility_never_connected():
    # False by default
    cleaner_no_del = OntCleaner(
        client=None,
        backup_manager=None,
        settings=CleanupSettings(delete_never_connected=False),
    )
    ont_never = OntRecord(
        frame=0,
        slot=1,
        port=0,
        ont_id=1,
        run_state="offline",
        never_connected=True,
    )
    eligible, _ = cleaner_no_del.evaluate_eligibility(ont_never)
    assert not eligible

    # True when configured
    cleaner_del = OntCleaner(
        client=None,
        backup_manager=None,
        settings=CleanupSettings(delete_never_connected=True),
    )
    eligible, _ = cleaner_del.evaluate_eligibility(ont_never)
    assert eligible


def test_execute_candidate_cleanup_dry_run():
    mock_client = MagicMock()
    mock_bm = MagicMock()
    settings = CleanupSettings(dry_run=True)
    cleaner = OntCleaner(mock_client, mock_bm, settings)

    ont = OntRecord(frame=0, slot=1, port=0, ont_id=1, run_state="offline", calculated_offline_days=45.0)
    candidate = CleanupCandidate(
        ont=ont,
        service_ports=[100, 101],
        eligible_for_deletion=True,
    )

    result = cleaner.execute_candidate_cleanup(candidate)
    assert result.success
    assert result.ont_deleted
    assert result.service_ports_deleted == [100, 101]
    # In dry-run, no commands should be sent to client
    mock_client.undo_service_port.assert_not_called()
    mock_client.delete_ont.assert_not_called()


def test_execute_candidate_cleanup_real(tmp_path):
    mock_client = MagicMock()
    mock_client.undo_service_port.return_value = (True, "Success")
    mock_client.delete_ont.return_value = (True, "Success")

    bm = BackupManager(str(tmp_path))
    settings = CleanupSettings(dry_run=False)
    cleaner = OntCleaner(mock_client, bm, settings)

    ont = OntRecord(frame=0, slot=1, port=0, ont_id=5, run_state="offline", sn="48575443AABBCCDD")
    candidate = CleanupCandidate(
        ont=ont,
        service_ports=[500, 501],
        raw_current_config="ont add 0 5 sn-auth ...\nservice-port 500 ...\nservice-port 501 ...",
        eligible_for_deletion=True,
    )

    result = cleaner.execute_candidate_cleanup(candidate)

    assert result.success
    assert result.ont_deleted
    assert result.service_ports_deleted == [500, 501]

    # Verify order of execution:
    # 1. undo service ports
    assert mock_client.undo_service_port.call_count == 2
    mock_client.undo_service_port.assert_any_call(500)
    mock_client.undo_service_port.assert_any_call(501)

    # 2. enter gpon interface
    mock_client.enter_gpon_interface.assert_called_once_with(0, 1)

    # 3. ont delete
    mock_client.delete_ont.assert_called_once_with(0, 5)

    # 4. exit interface
    mock_client.exit_interface_mode.assert_called_once()


def test_execute_single_rollback(tmp_path):
    bm = BackupManager(str(tmp_path))
    settings = CleanupSettings(dry_run=False)
    coordinator = MultiOltCoordinator(bm, settings, is_demo=True)

    target = OltTarget(host="192.168.1.100", name="OLT Central")
    rb_cmds = [
        "config",
        "interface gpon 0/1",
        'ont add 0 5 sn-auth "48575443AABBCCDD" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "cliente.teste"',
        "quit",
        "service-port 500 vlan 100 gpon 0/1/0 ont 5 gemport 1 multi-service user-vlan 100 tag-transform translate",
    ]
    ok, msg = coordinator.execute_rollback(target, rb_cmds)
    assert ok
    assert "ont add 0 5" in msg
    assert "service-port 500" in msg


