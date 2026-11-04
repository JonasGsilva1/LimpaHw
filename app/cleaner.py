import logging
from datetime import datetime
from typing import List, Optional, Tuple, Callable
from app.models import OntRecord, CleanupCandidate, CleanupResult, OltTarget
from app.config import CleanupSettings, OltSettings
from app.backup_manager import BackupManager
from app.olt_connection import HuaweiOltClient
from app.mock_client import MockOltClient
from app.parsers import (
    parse_board_slots,
    parse_ont_summary,
    parse_ont_detail,
    parse_service_ports_from_config,
    parse_service_ports_from_table,
)

logger = logging.getLogger("limpahw.cleaner")


class OntCleaner:
    def __init__(
        self,
        client,
        backup_manager: BackupManager,
        settings: CleanupSettings,
        olt_host: str = "",
        olt_name: str = "",
    ):
        self.client = client
        self.backup_manager = backup_manager
        self.settings = settings
        self.olt_host = olt_host
        self.olt_name = olt_name

    def evaluate_eligibility(self, ont: OntRecord) -> Tuple[bool, str]:
        """
        Determines whether an ONT should be deleted based on settings.
        Returns (is_eligible, reason).
        """
        if ont.run_state.lower() != "offline":
            return False, f"ONT online (Estado: {ont.run_state})"

        # Check description against whitelist
        desc_upper = (ont.customer_login or ont.description or "").upper()
        for white_word in self.settings.description_whitelist:
            if white_word in desc_upper:
                return False, f"Protegida por Whitelist (termo: '{white_word}' na descrição)"

        # Check if never connected
        if ont.never_connected:
            if self.settings.delete_never_connected:
                return True, "Nunca conectou (configurado para exclusão)"
            else:
                return False, "Nunca conectou (ignorado por configuração)"

        # Check offline days
        if ont.calculated_offline_days is None:
            return False, "Não foi possível determinar a data de queda da ONT"

        days_int = int(ont.calculated_offline_days)
        limit_int = int(self.settings.days_offline_threshold)
        if days_int >= limit_int:
            return (
                True,
                f"Offline há {days_int} dias (limite: >= {limit_int} dias)",
            )
        else:
            return (
                False,
                f"Offline há {days_int} dias (inferior ao limite de {limit_int} dias)",
            )

    def scan_port(
        self,
        frame: int,
        slot: int,
        port: int,
        reference_time: Optional[datetime] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List[CleanupCandidate]:
        """
        Scans a specific GPON port (frame/slot/port), inspects all offline ONTs,
        and returns candidates for cleanup.
        """
        if reference_time is None:
            reference_time = datetime.now()

        candidates: List[CleanupCandidate] = []
        loc_str = f"{frame}/{slot}/{port}"
        if progress_callback:
            progress_callback(f"Verificando porta GPON 0/{slot}/{port} ({self.olt_name or self.olt_host})...")

        # 1. Get summary for port
        cmd_summary = f"display ont info summary 0/{slot}/{port}"
        summary_output = self.client.send_command(cmd_summary)
        if "parameter error" in summary_output.lower() or "unknown command" in summary_output.lower():
            cmd_summary = f"display ont info summary 0 {slot} {port}"
            summary_output = self.client.send_command(cmd_summary)

        ont_summaries = parse_ont_summary(
            summary_output, frame=frame, slot=slot, port=port, reference_time=reference_time
        )

        offline_count = sum(1 for o in ont_summaries if o.run_state.lower() == "offline")
        logger.info(
            f"OLT {self.olt_host} Porta {loc_str}: {len(ont_summaries)} ONTs no total ({offline_count} offline)."
        )
        if len(ont_summaries) == 0:
            snippet = " | ".join(l.strip() for l in summary_output.splitlines() if l.strip())[:200]
            logger.debug(f"Saída bruta porta {loc_str} (0 ONTs): {snippet}")

        # 2. Inspect each offline ONT
        for ont in ont_summaries:
            ont.olt_host = self.olt_host
            ont.olt_name = self.olt_name

            if ont.run_state.lower() != "offline":
                continue

            # If summary didn't already provide offline duration/timestamps or SN, query detailed info
            if (ont.calculated_offline_days is None and not ont.never_connected) or not ont.sn:
                if progress_callback:
                    progress_callback(
                        f"Analisando detalhes da ONT {ont.location} em {self.olt_name or self.olt_host}..."
                    )
                cmd_info = f"display ont info 0 {slot} {port} {ont.ont_id}"
                info_output = self.client.send_command(cmd_info)
                parse_ont_detail(info_output, ont, reference_time=reference_time)

            eligible, reason = self.evaluate_eligibility(ont)
            if not eligible:
                continue

            # If eligible and we haven't queried detailed info yet (e.g. to get MAC address if available), query it
            if not ont.raw_info:
                cmd_info = f"display ont info 0 {slot} {port} {ont.ont_id}"
                info_output = self.client.send_command(cmd_info)
                parse_ont_detail(info_output, ont, reference_time=reference_time)
                # Re-evaluate in case description was enriched
                eligible, reason = self.evaluate_eligibility(ont)
                if not eligible:
                    continue

            service_ports: List[int] = []
            raw_cfg = ""

            if progress_callback:
                progress_callback(f"Localizando service-port para ONT {ont.location} ({ont.customer_login})...")

            # Step 1: display current-configuration ont 0/{slot}/{port} {ont_id}
            cmd_cfg = f"display current-configuration ont 0/{slot}/{port} {ont.ont_id}"
            raw_cfg = self.client.send_command(cmd_cfg)
            cfg_service_ports = parse_service_ports_from_config(raw_cfg)

            # Cross-check with display service-port port ...
            cmd_sp = f"display service-port port 0/{slot}/{port} ont {ont.ont_id}"
            sp_output = self.client.send_command(cmd_sp)
            tbl_records = parse_service_ports_from_table(sp_output)
            tbl_service_ports = [r.index for r in tbl_records]

            # Combine unique service ports
            combined_sp = sorted(list(set(cfg_service_ports + tbl_service_ports)))
            service_ports = combined_sp

            candidate = CleanupCandidate(
                ont=ont,
                service_ports=service_ports,
                raw_current_config=raw_cfg,
                eligible_for_deletion=True,
                skip_reason="",
                selected_by_user=True,
            )
            candidates.append(candidate)

        return candidates

    def scan(
        self,
        frame: int = 0,
        slot: Optional[int] = None,
        port: Optional[int] = None,
        reference_time: Optional[datetime] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List[CleanupCandidate]:
        """
        Scans OLT ports based on parameters.
        - If slot and port provided: scans only that port.
        - If slot provided, port=None: scans all 16 ports of the slot.
        - If slot=None: queries active GPON boards with 'display board 0' and scans all.
        """
        all_candidates: List[CleanupCandidate] = []

        if slot is not None and port is not None:
            return self.scan_port(frame, slot, port, reference_time, progress_callback)

        slots_to_scan: List[int] = []
        if slot is not None:
            slots_to_scan = [slot]
        else:
            if progress_callback:
                progress_callback(f"Consultando placas GPON ativas em {self.olt_name or self.olt_host}...")
            board_output = self.client.send_command(f"display board {frame}")
            slots_to_scan = parse_board_slots(board_output)
            logger.info(f"Slots GPON detectados em {self.olt_host}: {slots_to_scan}")
            if not slots_to_scan:
                logger.warning(
                    f"Nenhum slot GPON detectado na OLT {self.olt_host}. "
                    f"Saída de 'display board {frame}':\n{board_output[:600]}"
                )

        for s in slots_to_scan:
            ports_to_scan = [port] if port is not None else list(range(0, 16))
            for p in ports_to_scan:
                cands = self.scan_port(frame, s, p, reference_time, progress_callback)
                all_candidates.extend(cands)

        return all_candidates

    def execute_candidate_cleanup(self, candidate: CleanupCandidate) -> CleanupResult:
        """
        Executes the deletion routine for a single candidate:
        1. Save backup and generate rollback script
        2. Undo all service-ports
        3. Enter interface gpon 0/{slot}
        4. ont delete {port} {ont_id}
        5. Return CleanupResult
        """
        ont = candidate.ont

        if not candidate.eligible_for_deletion:
            return CleanupResult(
                ont=ont,
                success=False,
                error_message=f"ONT não elegível: {candidate.skip_reason}",
            )

        # Generate rollback commands
        rollback_cmds = []
        if candidate.raw_current_config:
            rollback_cmds = self.backup_manager.generate_rollback_commands(candidate)

        # In DRY-RUN mode, simulate without sending modifying commands to OLT
        if self.settings.dry_run:
            logger.info(f"[SIMULACAO] Deletaria ONT {ont.location} e service-ports {candidate.service_ports}")
            return CleanupResult(
                ont=ont,
                service_ports_deleted=list(candidate.service_ports),
                ont_deleted=True,
                success=True,
                error_message="[SIMULACAO] Nenhuma alteração foi realizada na OLT.",
                rollback_commands=rollback_cmds,
            )

        # Real Execution:
        # Step 0: Ensure we have current config and backup saved
        if not candidate.raw_current_config:
            cmd_cfg = f"display current-configuration ont 0/{ont.slot}/{ont.port} {ont.ont_id}"
            candidate.raw_current_config = self.client.send_command(cmd_cfg)
            rollback_cmds = self.backup_manager.generate_rollback_commands(candidate)

        backup_file = self.backup_manager.save_candidate_backup(candidate)
        rollback_file = self.backup_manager.save_rollback_script(candidate)

        sp_deleted: List[int] = []
        sp_failed: List[int] = []

        # Step 2: Undo service-ports
        for sp_id in candidate.service_ports:
            success, msg = self.client.undo_service_port(sp_id)
            if success:
                sp_deleted.append(sp_id)
            else:
                sp_failed.append(sp_id)
                logger.error(
                    f"Falha ao remover service-port {sp_id} para ONT {ont.location}: {msg}"
                )

        if sp_failed:
            return CleanupResult(
                ont=ont,
                service_ports_deleted=sp_deleted,
                service_ports_failed=sp_failed,
                ont_deleted=False,
                success=False,
                error_message=f"Falha ao remover service-ports {sp_failed}. Operação abortada para evitar inconsistência.",
                backup_file=backup_file,
                rollback_file=rollback_file,
                rollback_commands=rollback_cmds,
            )

        # Step 3: Enter interface gpon
        try:
            self.client.enter_gpon_interface(ont.frame, ont.slot)
        except Exception as e:
            return CleanupResult(
                ont=ont,
                service_ports_deleted=sp_deleted,
                ont_deleted=False,
                success=False,
                error_message=f"Erro ao entrar na interface gpon {ont.frame}/{ont.slot}: {e}",
                backup_file=backup_file,
                rollback_file=rollback_file,
                rollback_commands=rollback_cmds,
            )

        # Step 4: Delete ONT
        del_success, del_msg = self.client.delete_ont(ont.port, ont.ont_id)

        # Step 5: Exit interface
        self.client.exit_interface_mode()

        if not del_success:
            return CleanupResult(
                ont=ont,
                service_ports_deleted=sp_deleted,
                ont_deleted=False,
                success=False,
                error_message=f"Falha no comando ont delete: {del_msg}",
                backup_file=backup_file,
                rollback_file=rollback_file,
                rollback_commands=rollback_cmds,
            )

        return CleanupResult(
            ont=ont,
            service_ports_deleted=sp_deleted,
            ont_deleted=True,
            success=True,
            backup_file=backup_file,
            rollback_file=rollback_file,
            rollback_commands=rollback_cmds,
        )


class MultiOltCoordinator:
    """
    Coordinates scanning and cleaning routines across multiple Huawei OLT targets.
    Supports direct SSH or JumpServer Bastion PAM proxying.
    """

    def __init__(
        self,
        backup_manager: BackupManager,
        settings: CleanupSettings,
        is_demo: bool = False,
        use_jumpserver: bool = False,
        jumpserver_host: str = "",
        jumpserver_port: int = 2222,
        jumpserver_user: str = "",
        jumpserver_password: str = "",
        jumpserver_account: str = "",
    ):
        self.backup_manager = backup_manager
        self.settings = settings
        self.is_demo = is_demo
        self.use_jumpserver = use_jumpserver
        self.jumpserver_host = jumpserver_host
        self.jumpserver_port = jumpserver_port
        self.jumpserver_user = jumpserver_user
        self.jumpserver_password = jumpserver_password
        self.jumpserver_account = jumpserver_account
        self.scan_errors: List[str] = []

    def _create_client(self, target: OltTarget):
        if self.is_demo:
            return MockOltClient()

        if self.use_jumpserver:
            from app.jumpserver_client import JumpServerOltClient
            # Prioritize IP (host) for JumpServer search, with name as fallback
            primary_target = target.host.strip() if target.host else target.name.strip()
            fallback_target = target.name.strip() if target.name and target.name.strip() != primary_target else ""
            return JumpServerOltClient(
                jumpserver_host=self.jumpserver_host,
                jumpserver_port=self.jumpserver_port,
                username=self.jumpserver_user,
                password=self.jumpserver_password,
                system_user=self.jumpserver_account,
                target_olt=primary_target,
                fallback_target=fallback_target,
                timeout=30,
            )

        olt_cfg = OltSettings(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            enable_password=target.enable_password,
        )
        return HuaweiOltClient(olt_cfg)

    def scan_all(
        self,
        targets: List[OltTarget],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List[CleanupCandidate]:
        """Scans all specified OLT targets and aggregates discovered candidates."""
        all_candidates: List[CleanupCandidate] = []

        for target in targets:
            if progress_callback:
                progress_callback(f"Conectando à OLT {target.display_name}...")

            client = self._create_client(target)
            try:
                client.connect()
                cleaner = OntCleaner(
                    client=client,
                    backup_manager=self.backup_manager,
                    settings=self.settings,
                    olt_host=target.host,
                    olt_name=target.name,
                )
                candidates = cleaner.scan(
                    frame=0,
                    slot=target.slot,
                    port=target.port_gpon,
                    progress_callback=progress_callback,
                )
                all_candidates.extend(candidates)
            except Exception as e:
                err_msg = f"Erro ao escanear OLT {target.display_name}: {e}"
                logger.error(err_msg)
                self.scan_errors.append(err_msg)
                if progress_callback:
                    progress_callback(err_msg)
            finally:
                client.disconnect()

        return all_candidates

    def execute_cleanups(
        self,
        targets_by_ip: dict,
        candidates: List[CleanupCandidate],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List[CleanupResult]:
        """
        Executes cleanup for the selected candidates grouped by OLT.
        """
        results: List[CleanupResult] = []

        # Group candidates by OLT host
        candidates_by_olt = {}
        for c in candidates:
            if not c.selected_by_user or not c.eligible_for_deletion:
                continue
            host = c.ont.olt_host
            candidates_by_olt.setdefault(host, []).append(c)

        for host, cands in candidates_by_olt.items():
            target = targets_by_ip.get(host)
            if not target:
                logger.warning(f"Target OLT não encontrado para host {host}")
                continue

            if progress_callback:
                progress_callback(f"Conectando à OLT {target.display_name} para executar exclusões...")

            client = self._create_client(target)
            try:
                client.connect()
                cleaner = OntCleaner(
                    client=client,
                    backup_manager=self.backup_manager,
                    settings=self.settings,
                    olt_host=target.host,
                    olt_name=target.name,
                )
                for cand in cands:
                    if progress_callback:
                        progress_callback(
                            f"Removendo ONT {cand.ont.location} ({cand.ont.customer_login}) em {target.display_name}..."
                        )
                    res = cleaner.execute_candidate_cleanup(cand)
                    results.append(res)
            except Exception as e:
                logger.error(f"Erro durante execução na OLT {target.display_name}: {e}")
                for cand in cands:
                    results.append(
                        CleanupResult(
                            ont=cand.ont,
                            success=False,
                            error_message=f"Erro de conexão com OLT: {e}",
                        )
                    )
            finally:
                client.disconnect()

        return results

    def execute_rollback(
        self,
        target: OltTarget,
        rollback_commands: List[str],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Connects to the target OLT and executes the individual rollback CLI commands
        to re-add the ONT and its service-ports.
        """
        if not rollback_commands:
            return False, "Nenhum comando de rollback disponível para este equipamento."

        if self.settings.dry_run and not self.is_demo:
            logger.info(f"[SIMULACAO ROLLBACK] Executaria em {target.display_name}: {rollback_commands}")
            return (
                True,
                "[SIMULAÇÃO / DRY-RUN] Comandos de rollback validados (mude para 'Deleção Real (OLT)' na barra lateral caso queira enviar os comandos à OLT real).\n"
                + "\n".join(f"-> {c}" for c in rollback_commands),
            )

        client = self._create_client(target)
        outputs: List[str] = []
        errors: List[str] = []

        try:
            if progress_callback:
                progress_callback(f"Conectando à OLT {target.display_name} para reincluir equipamento...")
            client.connect()

            for cmd in rollback_commands:
                clean_cmd = cmd.strip()
                if not clean_cmd or clean_cmd.startswith("#") or clean_cmd.lower() == "config":
                    continue
                if progress_callback:
                    progress_callback(f"Executando na OLT: {clean_cmd}")
                ok, out = client.execute_config_command(clean_cmd)
                outputs.append(f"$ {clean_cmd}\n{out}")
                if not ok:
                    errors.append(f"Comando '{clean_cmd}' retornou erro: {out}")

            if errors:
                return False, "\n".join(errors) + "\n\nLog completo:\n" + "\n".join(outputs)

            return True, "Equipamento e service-ports reincluídos com sucesso na OLT!\n\n" + "\n".join(outputs)
        except Exception as e:
            logger.error(f"Erro ao executar rollback na OLT {target.display_name}: {e}")
            return False, f"Erro de conexão/execução na OLT {target.display_name}: {e}"
        finally:
            client.disconnect()

