import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import pandas as pd
from datetime import datetime
from typing import List, Union, BinaryIO
from app.models import OltTarget, CleanupResult


def create_template_excel(output_path: str = "modelo_olts.xlsx") -> str:
    """
    Creates an Excel template file with instructions and sample rows
    for importing OLT connection credentials and optional scan filters.
    """
    wb = openpyxl.Workbook()
    ws_olts = wb.active
    ws_olts.title = "OLTs"

    # Define styles
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    headers = [
        "Nome_OLT",
        "IP_OLT",
        "Porta_SSH",
        "Usuario",
        "Senha",
        "Senha_Enable",
        "Slot",
        "Porta_PON",
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws_olts.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    # Sample rows
    sample_data = [
        ["OLT Central", "192.168.10.1", 22, "admin", "SuaSenhaAqui", "", "", ""],
        ["OLT Bairro Norte", "192.168.20.1", 22, "admin", "SuaSenhaAqui", "", 1, 0],
    ]

    for row_idx, row_values in enumerate(sample_data, 2):
        for col_idx, val in enumerate(row_values, 1):
            cell = ws_olts.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            if col_idx in (3, 7, 8):
                cell.alignment = center_align
            else:
                cell.alignment = left_align

    # Adjust column widths
    column_widths = {
        "A": 22,  # Nome_OLT
        "B": 18,  # IP_OLT
        "C": 12,  # Porta_SSH
        "D": 16,  # Usuario
        "E": 20,  # Senha
        "F": 16,  # Senha_Enable
        "G": 10,  # Slot
        "H": 14,  # Porta_PON
    }
    for col_letter, width in column_widths.items():
        ws_olts.column_dimensions[col_letter].width = width

    # Tab 2: Instructions
    ws_info = wb.create_sheet(title="Instruções")
    info_title_font = Font(name="Calibri", size=14, bold=True, color="1F497D")
    info_bold = Font(name="Calibri", size=11, bold=True)
    info_regular = Font(name="Calibri", size=11)

    ws_info["A1"] = "INSTRUÇÕES PARA PREENCHIMENTO DA PLANILHA DE OLTS"
    ws_info["A1"].font = info_title_font

    instructions = [
        ("", ""),
        ("Nome_OLT", "Nome amigável ou identificação da OLT (Ex: OLT Matriz, POP Centro)."),
        ("IP_OLT", "Endereço IP acessível da OLT Huawei via SSH (Obrigatório)."),
        ("Porta_SSH", "Porta do serviço SSH (padrão 22)."),
        ("Usuario", "Usuário de acesso à OLT com privilégios de configuração (Obrigatório)."),
        ("Senha", "Senha de autenticação SSH (Obrigatório)."),
        ("Senha_Enable", "Senha do modo enable/super (opcional se não houver senha separada)."),
        ("Slot", "Número da placa GPON específica (opcional: deixe vazio para escanear todas as placas)."),
        ("Porta_PON", "Número da porta PON específica (opcional: deixe vazio para escanear todas as portas)."),
    ]

    for row_idx, (col_name, desc) in enumerate(instructions, 2):
        c1 = ws_info.cell(row=row_idx, column=1, value=col_name)
        c2 = ws_info.cell(row=row_idx, column=2, value=desc)
        c1.font = info_bold
        c2.font = info_regular

    ws_info.column_dimensions["A"].width = 20
    ws_info.column_dimensions["B"].width = 75

    wb.save(output_path)
    return output_path


def read_olts_from_excel(file_source: Union[str, BinaryIO]) -> List[OltTarget]:
    """
    Reads OLT targets from an Excel file (path or uploaded buffer/bytes).
    Normalizes column headers and extracts valid OltTarget objects.
    """
    df = pd.read_excel(file_source, sheet_name=0)

    # Normalize headers
    col_map = {}
    for col in df.columns:
        clean = (
            str(col)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("á", "a")
            .replace("ã", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
        )
        col_map[col] = clean

    df = df.rename(columns=col_map)

    # Column mapping aliases
    def find_col(candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    ip_col = find_col(["ip_olt", "ip", "host", "endereco_ip"])
    user_col = find_col(["usuario", "user", "username", "login"])
    pass_col = find_col(["senha", "password", "pass"])
    port_col = find_col(["porta_ssh", "porta", "port", "ssh_port"])
    name_col = find_col(["nome_olt", "nome", "name", "olt"])
    enable_col = find_col(["senha_enable", "enable", "secret"])
    slot_col = find_col(["slot", "placa"])
    pon_col = find_col(["porta_pon", "pon", "porta_gpon"])

    if not ip_col and not name_col:
        raise ValueError(
            f"A planilha precisa conter pelo menos a coluna 'IP_OLT' ou 'Nome_OLT'. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    targets: List[OltTarget] = []

    for _, row in df.iterrows():
        ip_val = str(row.get(ip_col, "")).strip() if ip_col else ""
        name_val = str(row.get(name_col, "")).strip() if name_col else ""

        if ip_val.lower() == "nan":
            ip_val = ""
        if name_val.lower() == "nan":
            name_val = ""

        if not ip_val and not name_val:
            continue

        # If IP is empty but name is provided (e.g. asset name in JumpServer)
        if not ip_val and name_val:
            ip_val = name_val

        user_val = str(row.get(user_col, "")).strip() if user_col else ""
        if user_val.lower() == "nan":
            user_val = ""

        pass_val = str(row.get(pass_col, "")).strip() if pass_col else ""
        if pass_val.lower() == "nan":
            pass_val = ""

        # Port
        port_val = 22
        if port_col and pd.notna(row.get(port_col)):
            try:
                port_val = int(row[port_col])
            except (ValueError, TypeError):
                port_val = 22

        # Enable
        enable_val = None
        if enable_col and pd.notna(row.get(enable_col)):
            val = str(row[enable_col]).strip()
            if val and val.lower() != "nan":
                enable_val = val

        # Slot filter
        slot_val = None
        if slot_col and pd.notna(row.get(slot_col)):
            try:
                slot_val = int(row[slot_col])
            except (ValueError, TypeError):
                slot_val = None

        # PON filter
        pon_val = None
        if pon_col and pd.notna(row.get(pon_col)):
            try:
                pon_val = int(row[pon_col])
            except (ValueError, TypeError):
                pon_val = None

        target = OltTarget(
            host=ip_val,
            port=port_val,
            username=user_val,
            password=pass_val,
            enable_password=enable_val,
            name=name_val,
            slot=slot_val,
            port_gpon=pon_val,
        )
        targets.append(target)

    return targets


def export_cleanup_report_excel(results: List[CleanupResult], output_path: str) -> str:
    """
    Exports the cleanup audit report to a formatted Excel file with:
    - Deleted ONTs with MAC, Customer Login/Description, Service-Ports
    - Embedded rollback CLI commands for each equipment
    - Summary tab with overall statistics
    """
    wb = openpyxl.Workbook()
    ws_data = wb.active
    ws_data.title = "Equipamentos_Deletados"

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    headers = [
        "Data_Hora_Exclusao",
        "OLT_Nome",
        "OLT_IP",
        "Localizacao",
        "Serial_Number",
        "MAC_Address",
        "Login_Cliente_Descricao",
        "Dias_Offline",
        "Service_Ports_Removidos",
        "Status",
        "Comandos_Rollback_CLI",
        "Arquivo_Rollback",
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws_data.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    success_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    failed_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for row_idx, res in enumerate(results, 2):
        ont = res.ont
        status_text = "Sucesso" if res.success else f"Falha: {res.error_message}"
        sp_str = ", ".join(map(str, res.service_ports_deleted)) if res.service_ports_deleted else "-"
        rollback_cmd_str = "\n".join(res.rollback_commands) if res.rollback_commands else ""

        row_vals = [
            now_str,
            ont.olt_name or "-",
            ont.olt_host or "-",
            ont.location,
            ont.sn or "-",
            ont.mac or "-",
            ont.customer_login or ont.description or "-",
            int(ont.calculated_offline_days) if ont.calculated_offline_days is not None else "-",
            sp_str,
            status_text,
            rollback_cmd_str,
            res.rollback_file or "-",
        ]

        for col_idx, val in enumerate(row_vals, 1):
            cell = ws_data.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            if col_idx in (1, 3, 4, 8, 9, 10):
                cell.alignment = center_align
            else:
                cell.alignment = left_align

            if col_idx == 10:  # Status
                cell.fill = success_fill if res.success else failed_fill

    # Set column widths
    widths = {
        "A": 20,  # Data
        "B": 18,  # OLT_Nome
        "C": 16,  # OLT_IP
        "D": 14,  # Local
        "E": 24,  # Serial
        "F": 18,  # MAC
        "G": 32,  # Login/Descricao
        "H": 14,  # Dias
        "I": 24,  # Service-Ports
        "J": 16,  # Status
        "K": 45,  # Rollback CLI
        "L": 35,  # Arquivo Rollback
    }
    for col_letter, width in widths.items():
        ws_data.column_dimensions[col_letter].width = width

    # Tab 2: Resumo
    ws_summary = wb.create_sheet(title="Resumo")
    ws_summary["A1"] = "RELATÓRIO DE AUDITORIA - LIMPAHW"
    ws_summary["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F497D")

    total_ops = len(results)
    total_success = sum(1 for r in results if r.success)
    total_failed = total_ops - total_success

    summary_rows = [
        ("", ""),
        ("Data da Operação:", now_str),
        ("Total de ONTs Processadas:", total_ops),
        ("Excluídas com Sucesso:", total_success),
        ("Falhas:", total_failed),
    ]

    for r_idx, (label, val) in enumerate(summary_rows, 2):
        c1 = ws_summary.cell(row=r_idx, column=1, value=label)
        c2 = ws_summary.cell(row=r_idx, column=2, value=val)
        c1.font = Font(name="Calibri", size=11, bold=True)
        c2.font = Font(name="Calibri", size=11)

    ws_summary.column_dimensions["A"].width = 28
    ws_summary.column_dimensions["B"].width = 30

    wb.save(output_path)
    return output_path

