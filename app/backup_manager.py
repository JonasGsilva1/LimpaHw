import os
import re
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Tuple
from app.models import CleanupCandidate, CleanupResult, OntRecord

logger = logging.getLogger("limpahw.backup")


class BackupManager:
    def __init__(self, backup_dir: str = "./backups", history_dir: str = "./historico_exclusoes"):
        self.backup_dir = backup_dir
        self.history_dir = history_dir
        os.makedirs(self.backup_dir, exist_ok=True)

    def save_candidate_backup(self, candidate: CleanupCandidate) -> str:
        """
        Saves the raw current configuration of an ONT to a backup file.
        Returns the path of the saved file.
        """
        ont = candidate.ont
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_ip = (ont.olt_host or "olt").replace(".", "_")
        filename = f"ont_{clean_ip}_0_{ont.slot}_{ont.port}_{ont.ont_id}_{timestamp}.cfg"
        filepath = os.path.join(self.backup_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# BACKUP ONT {ont.location}\n")
            f.write(f"# OLT: {ont.olt_name} ({ont.olt_host})\n")
            f.write(f"# Data: {datetime.now().isoformat()}\n")
            f.write(f"# SN: {ont.sn}\n")
            f.write(f"# MAC: {ont.mac or '-'}\n")
            f.write(f"# Login/Descricao: {ont.customer_login or ont.description}\n")
            f.write(f"# Dias Offline: {int(ont.calculated_offline_days) if ont.calculated_offline_days is not None else '-'}\n")
            f.write(f"# Service-Ports identificados: {candidate.service_ports}\n")
            f.write("#" + "=" * 60 + "\n\n")
            f.write(candidate.raw_current_config)

        candidate.backup_file_path = filepath
        logger.info(f"Backup salvo para ONT {ont.location}: {filepath}")
        return filepath

    def generate_rollback_commands(self, candidate: CleanupCandidate) -> List[str]:
        """
        Generates Huawei CLI commands to restore the ONT and its service-ports.
        Extracts configuration lines from raw_current_config.
        """
        ont = candidate.ont
        commands: List[str] = ["config"]

        config_lines = candidate.raw_current_config.splitlines()
        ont_commands: List[str] = []
        sp_commands: List[str] = []

        in_ont_section = False
        in_sp_section = False

        for line in config_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if "[ont-configuration]" in stripped or "<ont-configuration>" in stripped:
                in_ont_section = True
                in_sp_section = False
                continue

            if "[service-port]" in stripped or "<service-port>" in stripped:
                in_ont_section = False
                in_sp_section = True
                continue

            if stripped == "return" or stripped.startswith("display "):
                continue

            if in_ont_section:
                if stripped.startswith("ont "):
                    ont_commands.append(stripped)
            elif in_sp_section:
                if stripped.startswith("service-port "):
                    sp_commands.append(stripped)
            else:
                if stripped.startswith("ont "):
                    ont_commands.append(stripped)
                elif stripped.startswith("service-port "):
                    sp_commands.append(stripped)

        if ont_commands:
            commands.append(f"interface gpon {ont.frame}/{ont.slot}")
            commands.extend(ont_commands)
            commands.append("quit")

        if sp_commands:
            commands.extend(sp_commands)

        return commands

    def save_rollback_script(self, candidate: CleanupCandidate) -> str:
        """
        Saves a standalone individual rollback script file for a specific ONT.
        """
        ont = candidate.ont
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_ip = (ont.olt_host or "olt").replace(".", "_")
        filename = (
            f"rollback_{clean_ip}_slot{ont.slot}_pon{ont.port}_ont{ont.ont_id}_{timestamp}.txt"
        )
        filepath = os.path.join(self.backup_dir, filename)

        commands = self.generate_rollback_commands(candidate)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# ============================================================\n")
            f.write(f"# SCRIPT DE RESTAURACAO (ROLLBACK INDIVIDUAL)\n")
            f.write(f"# OLT: {ont.olt_name} ({ont.olt_host})\n")
            f.write(f"# Localizacao: GPON 0/{ont.slot}/{ont.port} ONT {ont.ont_id}\n")
            f.write(f"# Login Cliente / Descricao: {ont.customer_login or ont.description}\n")
            f.write(f"# SN: {ont.sn} | MAC: {ont.mac or '-'}\n")
            f.write(f"# Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# ============================================================\n\n")
            f.write("\n".join(commands))
            f.write("\n")

        candidate.rollback_file_path = filepath
        logger.info(f"Rollback gerado para ONT {ont.location}: {filepath}")
        return filepath

    def save_session_rollback(self, candidates: List[CleanupCandidate]) -> Optional[str]:
        """
        Saves a combined rollback script for all candidates processed in the session.
        """
        if not candidates:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.backup_dir, f"session_rollback_{timestamp}.txt")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# SCRIPT DE RESTAURACAO DA SESSAO\n")
            f.write(f"# Gerado em: {datetime.now().isoformat()}\n")
            f.write(f"# Total de ONTs no rollback: {len(candidates)}\n")
            f.write("#" + "=" * 60 + "\n\n")
            f.write("config\n")

            for candidate in candidates:
                commands = self.generate_rollback_commands(candidate)
                if commands and commands[0] == "config":
                    commands = commands[1:]
                ont = candidate.ont
                f.write(
                    f"\n# --- Restaura ONT {ont.location} "
                    f"(OLT: {ont.olt_host}, Login: {ont.customer_login or ont.description}, SN: {ont.sn}) ---\n"
                )
                f.write("\n".join(commands))
                f.write("\n")

        return filepath

    def save_audit_report(self, results: List[CleanupResult]) -> str:
        """
        Saves a summary JSON audit report of the execution.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.backup_dir, f"audit_report_{timestamp}.json")

        report_data = []
        for r in results:
            report_data.append(
                {
                    "olt_host": r.ont.olt_host,
                    "olt_name": r.ont.olt_name,
                    "location": r.ont.location,
                    "sn": r.ont.sn,
                    "mac": r.ont.mac,
                    "customer_login": r.ont.customer_login or r.ont.description,
                    "offline_days": r.ont.calculated_offline_days,
                    "service_ports_deleted": r.service_ports_deleted,
                    "service_ports_failed": r.service_ports_failed,
                    "ont_deleted": r.ont_deleted,
                    "success": r.success,
                    "error_message": r.error_message,
                    "backup_file": r.backup_file,
                    "rollback_file": r.rollback_file,
                    "rollback_commands": r.rollback_commands,
                }
            )

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        return filepath

    def save_deletion_history_txt(
        self,
        results: List[CleanupResult],
        execution_time: Optional[datetime] = None,
        is_dry_run: bool = False,
    ) -> Optional[str]:
        """
        Saves a mandatory structured .txt log of deleted ONTs inside `self.history_dir`
        (e.g. historico_exclusoes/2026-11-19_21-55-00.txt).
        The file is both human-readable and re-importable for individual 1-click rollback.
        """
        processed = [r for r in results if r.success and (r.ont_deleted or r.rollback_commands)]
        if not processed:
            return None

        os.makedirs(self.history_dir, exist_ok=True)
        now = execution_time or datetime.now()
        filename = now.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
        filepath = os.path.join(self.history_dir, filename)

        # Avoid overwriting if two cleanups happen within the same second
        suffix = 1
        while os.path.exists(filepath):
            filename = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{suffix}.txt"
            filepath = os.path.join(self.history_dir, filename)
            suffix += 1

        modo_str = "SIMULACAO" if is_dry_run else "REAL"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# ============================================================\n")
            f.write("# LIMPAHW - REGISTRO DE EXCLUSOES DE ONTS\n")
            f.write(f"# DATA_HORA: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# MODO: {modo_str}\n")
            f.write(f"# TOTAL_ONTS: {len(processed)}\n")
            f.write("# ============================================================\n\n")

            for res in processed:
                ont = res.ont
                dias_val = str(int(ont.calculated_offline_days)) if ont.calculated_offline_days is not None else "Nunca"
                sp_str = ", ".join(map(str, res.service_ports_deleted)) if res.service_ports_deleted else "-"
                f.write("=== ONT_INICIO ===\n")
                f.write(f"OLT_NOME: {ont.olt_name or '-'}\n")
                f.write(f"OLT_IP: {ont.olt_host or '-'}\n")
                f.write(f"LOCALIZACAO: {ont.location}\n")
                f.write(f"LOGIN_DESCRICAO: {ont.customer_login or ont.description or '-'}\n")
                f.write(f"SERIAL_SN: {ont.sn or '-'}\n")
                f.write(f"MAC: {ont.mac or '-'}\n")
                f.write(f"DIAS_OFFLINE: {dias_val}\n")
                f.write(f"SERVICE_PORTS: {sp_str}\n")
                f.write(f"MOTIVO_QUEDA: {ont.last_down_cause or '-'}\n")
                f.write(f"STATUS: {'SUCESSO' if res.success else 'FALHA'}\n")
                f.write("--- ROLLBACK_COMANDOS ---\n")
                for cmd in res.rollback_commands:
                    f.write(f"{cmd}\n")
                f.write("=== ONT_FIM ===\n\n")

        logger.info(f"Histórico obrigatório de exclusões salvo em: {filepath}")
        return filepath

    def list_deletion_history_files(self) -> List[str]:
        """Returns paths of saved .txt deletion history files sorted newest-first."""
        if not os.path.isdir(self.history_dir):
            return []
        files = [
            os.path.join(self.history_dir, fn)
            for fn in os.listdir(self.history_dir)
            if fn.lower().endswith(".txt")
        ]
        files.sort(reverse=True)
        return files

    @staticmethod
    def parse_deletion_history_txt(content: str) -> Tuple[Dict[str, str], List[CleanupResult]]:
        """
        Parses a deletion history .txt file generated by `save_deletion_history_txt()`
        back into (header_metadata, list_of_CleanupResult) for individual rollback.
        """
        metadata: Dict[str, str] = {}
        results: List[CleanupResult] = []

        in_block = False
        in_cmds = False
        fields: Dict[str, str] = {}
        cmds: List[str] = []

        for raw_line in content.splitlines():
            line = raw_line.strip()

            if not in_block:
                if line.startswith("#") and ":" in line:
                    k, v = line.lstrip("#").split(":", 1)
                    k_clean = k.strip().upper()
                    if k_clean in ("DATA_HORA", "MODO", "TOTAL_ONTS"):
                        metadata[k_clean] = v.strip()
                elif line == "=== ONT_INICIO ===":
                    in_block = True
                    in_cmds = False
                    fields = {}
                    cmds = []
                continue

            if line == "=== ONT_FIM ===":
                loc = fields.get("LOCALIZACAO", "0/0/0:0")
                m_loc = re.match(r"(\d+)/(\d+)/(\d+):(\d+)", loc)
                frame, slot, port, ont_id = (
                    (int(m_loc.group(1)), int(m_loc.group(2)), int(m_loc.group(3)), int(m_loc.group(4)))
                    if m_loc
                    else (0, 0, 0, 0)
                )

                olt_name = fields.get("OLT_NOME", "")
                if olt_name == "-":
                    olt_name = ""
                olt_ip = fields.get("OLT_IP", "")
                if olt_ip == "-":
                    olt_ip = ""
                login_desc = fields.get("LOGIN_DESCRICAO", "")
                if login_desc == "-":
                    login_desc = ""
                sn = fields.get("SERIAL_SN", "")
                if sn == "-":
                    sn = ""
                mac = fields.get("MAC", "")
                if mac == "-":
                    mac = ""
                motivo = fields.get("MOTIVO_QUEDA", "")
                if motivo == "-":
                    motivo = ""

                dias_raw = fields.get("DIAS_OFFLINE", "")
                calc_days: Optional[float] = None
                try:
                    calc_days = float(dias_raw)
                except (ValueError, TypeError):
                    calc_days = None

                sp_raw = fields.get("SERVICE_PORTS", "")
                sp_list = [int(x.strip()) for x in sp_raw.split(",") if x.strip().isdigit()]

                ont = OntRecord(
                    frame=frame,
                    slot=slot,
                    port=port,
                    ont_id=ont_id,
                    olt_host=olt_ip,
                    olt_name=olt_name,
                    sn=sn,
                    mac=mac,
                    description=login_desc,
                    customer_login=login_desc,
                    run_state="offline",
                    last_down_cause=motivo,
                    calculated_offline_days=calc_days,
                )
                results.append(
                    CleanupResult(
                        ont=ont,
                        service_ports_deleted=sp_list,
                        ont_deleted=True,
                        success=fields.get("STATUS", "SUCESSO").upper() == "SUCESSO",
                        rollback_commands=cmds,
                    )
                )
                in_block = False
                in_cmds = False
                continue

            if line == "--- ROLLBACK_COMANDOS ---":
                in_cmds = True
                continue

            if in_cmds:
                if line:
                    cmds.append(line)
            elif ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip().upper()] = v.strip()

        return metadata, results
