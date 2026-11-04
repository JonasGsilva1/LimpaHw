import os
import openpyxl
from app.models import OltTarget, OntRecord, CleanupResult
from app.excel_manager import (
    create_template_excel,
    read_olts_from_excel,
    export_cleanup_report_excel,
)


def test_create_and_read_template_excel(tmp_path):
    template_path = os.path.join(tmp_path, "test_modelo_olts.xlsx")
    create_template_excel(template_path)
    assert os.path.exists(template_path)

    # Read the created template
    targets = read_olts_from_excel(template_path)
    assert len(targets) == 2
    assert targets[0].name == "OLT Central"
    assert targets[0].host == "192.168.10.1"
    assert targets[0].username == "admin"
    assert targets[1].slot == 1
    assert targets[1].port_gpon == 0


def test_export_cleanup_report_excel(tmp_path):
    report_path = os.path.join(tmp_path, "relatorio_teste.xlsx")
    ont = OntRecord(
        frame=0,
        slot=1,
        port=0,
        ont_id=3,
        run_state="offline",
        sn="48575443AABBCCDD",
        mac="00e0-fc11-2233",
        description="cliente_login_teste",
        olt_host="192.168.1.100",
        olt_name="OLT Matriz",
        calculated_offline_days=45.0,
    )
    result = CleanupResult(
        ont=ont,
        service_ports_deleted=[1050, 1051],
        ont_deleted=True,
        success=True,
        rollback_commands=["config", "interface gpon 0/1", "ont add ...", "quit"],
        rollback_file="backups/rollback_test.txt",
    )

    export_cleanup_report_excel([result], report_path)
    assert os.path.exists(report_path)

    # Verify content with openpyxl
    wb = openpyxl.load_workbook(report_path)
    assert "Equipamentos_Deletados" in wb.sheetnames
    assert "Resumo" in wb.sheetnames

    ws = wb["Equipamentos_Deletados"]
    assert ws.cell(row=2, column=2).value == "OLT Matriz"
    assert ws.cell(row=2, column=3).value == "192.168.1.100"
    assert ws.cell(row=2, column=5).value == "48575443AABBCCDD"
    assert ws.cell(row=2, column=6).value == "00e0-fc11-2233"
    assert ws.cell(row=2, column=7).value == "cliente_login_teste"
    assert ws.cell(row=2, column=10).value == "Sucesso"

