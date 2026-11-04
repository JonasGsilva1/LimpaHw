import os
import json
import logging
from datetime import datetime
from typing import List, Optional
from app.models import CleanupCandidate, CleanupResult

logger = logging.getLogger("limpahw.backup")


class BackupManager:
    def __init__(self, backup_dir: str = "./backups"):
        self.backup_dir = backup_dir
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
