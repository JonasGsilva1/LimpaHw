import os
import sys
import argparse
import logging
from typing import Optional, List
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm

from app.config import OltSettings, CleanupSettings
from app.olt_connection import HuaweiOltClient
from app.mock_client import MockOltClient
from app.backup_manager import BackupManager
from app.cleaner import OntCleaner, MultiOltCoordinator
from app.models import CleanupCandidate, CleanupResult, OltTarget
from app.excel_manager import (
    create_template_excel,
    read_olts_from_excel,
    export_cleanup_report_excel,
)

# Configure console safely for Windows terminal
console = Console(force_terminal=True)


def setup_logger(log_dir: str = "./logs", verbose: bool = False):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "limpa_hw.log")

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def print_banner():
    banner_text = """[bold cyan]
  ==============================================================
                    LIMPA-HW : HUAWEI GPON OLT                  
                Automacao de Limpeza de ONTs Offline            
  ==============================================================[/bold cyan]
    [dim]Compativel com SmartAX MA5608T / MA5680T / MA5800 / MA5800-X7[/dim]
    """
    console.print(Panel(banner_text, border_style="cyan"))


def display_candidates_table(candidates: List[CleanupCandidate]):
    table = Table(
        title="[bold yellow]ONTs Offline Identificadas[/bold yellow]",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Item", justify="center", style="dim", width=5)
    table.add_column("OLT", style="cyan")
    table.add_column("Local", style="cyan", no_wrap=True)
    table.add_column("Login / Descrição (ERP)", style="white bold")
    table.add_column("Serial (SN) / MAC", style="blue")
    table.add_column("Última Queda", style="dim")
    table.add_column("Dias Off", justify="right", style="bold")
    table.add_column("Service-Ports", style="green")
    table.add_column("Status / Ação", style="bold")

    for i, c in enumerate(candidates, 1):
        ont = c.ont
        olt_str = ont.olt_name or ont.olt_host or "Local"
        loc = ont.location
        sn_mac = f"{ont.sn or '-'}\nMAC: {ont.mac or '-'}"
        login_str = ont.customer_login or ont.description or "-"
        down_info = f"{ont.last_down_time or '-'}\n({ont.last_down_cause or '-'})"

        days_str = (
            f"{int(ont.calculated_offline_days)} d"
            if ont.calculated_offline_days is not None
            else ("Nunca" if ont.never_connected else "-")
        )

        sp_str = ", ".join(map(str, c.service_ports)) if c.service_ports else "-"

        if c.eligible_for_deletion:
            action_status = "[bold red]DELETAR[/bold red]" if c.selected_by_user else "[yellow]DESMARCADA[/yellow]"
        else:
            action_status = f"[dim yellow]IGNORADO[/dim yellow]\n[dim]({c.skip_reason})[/dim]"

        table.add_row(
            str(i), olt_str, loc, login_str, sn_mac, down_info, days_str, sp_str, action_status
        )

    console.print(table)


def run_cli():
    parser = argparse.ArgumentParser(
        description="LimpaHW: Automação para remoção de ONTs Huawei offline"
    )
    parser.add_argument(
        "--excel",
        help="Caminho da planilha Excel (.xlsx) contendo as OLTs (IP, usuário, senha, etc.)",
    )
    parser.add_argument(
        "--gerar-modelo",
        action="store_true",
        help="Gera a planilha modelo 'modelo_olts.xlsx' e encerra",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Inicia a interface Web (Streamlit)",
    )
    parser.add_argument("--host", help="IP ou Hostname da OLT Huawei (caso não use planilha)")
    parser.add_argument("--port", type=int, help="Porta SSH (padrão 22)")
    parser.add_argument("--user", help="Usuário SSH")
    parser.add_argument("--password", help="Senha SSH")
    parser.add_argument(
        "--time",
        type=int,
        help="Valor do período limite de equipamento offline em número inteiro (ex: 30, 4, 2)",
    )
    parser.add_argument(
        "--unit",
        choices=["dias", "semanas", "meses"],
        default="dias",
        help="Unidade de tempo para o limite offline (padrão: dias)",
    )
    parser.add_argument(
        "--jumpserver",
        action="store_true",
        help="Conecta via JumpServer Bastion (SSH Proxy)",
    )
    parser.add_argument(
        "--js-host",
        default=os.getenv("JUMPSERVER_HOST", ""),
        help="Host do JumpServer (ex: jump.provedor.com.br)",
    )
    parser.add_argument(
        "--js-port",
        type=int,
        default=int(os.getenv("JUMPSERVER_PORT", "2222")),
        help="Porta SSH do JumpServer (padrão: 2222)",
    )
    parser.add_argument(
        "--js-user",
        default=os.getenv("JUMPSERVER_USER", ""),
        help="Seu usuário corporativo do JumpServer",
    )
    parser.add_argument(
        "--js-pass",
        default=os.getenv("JUMPSERVER_PASSWORD", ""),
        help="Sua senha corporativa do JumpServer",
    )
    parser.add_argument(
        "--js-account",
        default=os.getenv("JUMPSERVER_ACCOUNT", ""),
        help="Conta de sistema da OLT no JumpServer (opcional)",
    )
    parser.add_argument("--slot", type=int, help="Filtrar placa/slot GPON específica (ex: 1)")
    parser.add_argument("--port-gpon", type=int, help="Filtrar porta PON específica (ex: 0)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Executa em modo simulação (não deleta nada na OLT)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Executa deleção REAL na OLT",
    )
    parser.add_argument(
        "--demo",
        "--mock",
        action="store_true",
        help="Executa em modo demonstração com OLT simulada (sem SSH real)",
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Não pede confirmação manual antes de deletar",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log detalhado")

    args = parser.parse_args()

    # Generate template requested
    if args.gerar_modelo:
        model_file = create_template_excel("modelo_olts.xlsx")
        console.print(f"[bold green][OK][/bold green] Planilha modelo criada em: [cyan]{os.path.abspath(model_file)}[/cyan]")
        return

    # Web launcher requested
    if args.web:
        console.print("[bold cyan]Iniciando Interface Web LimpaHW (Streamlit)...[/bold cyan]")
        import subprocess
        from pathlib import Path
        root_dir = Path(__file__).resolve().parent.parent
        web_app_script = str(root_dir / "app" / "web_app.py")
        subprocess.run([sys.executable, "-m", "streamlit", "run", web_app_script])
        return

    # Load defaults from config / .env
    cleanup_settings = CleanupSettings()

    # Configure time threshold
    if args.time is not None:
        cleanup_settings.set_time_threshold(args.time, args.unit)
    else:
        # If not provided, we can ask in interactive mode or use default
        pass

    if args.no_dry_run:
        cleanup_settings.dry_run = False
    elif args.dry_run:
        cleanup_settings.dry_run = True

    setup_logger(cleanup_settings.log_dir, verbose=args.verbose)
    print_banner()

    is_mock = args.demo
    backup_mgr = BackupManager(cleanup_settings.backup_dir)

    # 1. Ask or confirm time threshold if interactive
    if args.time is None and not args.auto_confirm:
        console.print("[bold yellow]Defina o período limite para considerar o equipamento offline:[/bold yellow]")
        val_str = Prompt.ask("Quantidade", default=str(int(cleanup_settings.time_value)))
        try:
            val = int(float(val_str))
        except ValueError:
            val = 30

        unit = Prompt.ask("Unidade de tempo", choices=["dias", "semanas", "meses"], default=cleanup_settings.time_unit)
        cleanup_settings.set_time_threshold(val, unit)

    # 2. Get OLT Targets (via Excel or Manual Host)
    targets: List[OltTarget] = []

    if args.excel:
        if not os.path.exists(args.excel):
            console.print(f"[bold red]Erro:[/bold red] Arquivo Excel '{args.excel}' não encontrado.")
            sys.exit(1)
        targets = read_olts_from_excel(args.excel)
        console.print(f"[green]Planilha Excel carregada:[/green] {len(targets)} OLT(s) encontradas.")
    elif not is_mock:
        # Prompt for Excel or Single OLT
        if not args.host:
            has_excel = Confirm.ask("Deseja importar uma planilha Excel com as OLTs?", default=True)
            if has_excel:
                excel_path = Prompt.ask("Caminho do arquivo Excel (.xlsx)", default="modelo_olts.xlsx")
                if not os.path.exists(excel_path):
                    create_template_excel(excel_path)
                    console.print(
                        f"[yellow]Arquivo modelo gerado em '{excel_path}'. Preencha os IPs e execute novamente.[/yellow]"
                    )
                    sys.exit(0)
                targets = read_olts_from_excel(excel_path)
            else:
                host = Prompt.ask("[bold green]Informe o IP da OLT Huawei[/bold green]")
                user = Prompt.ask("[bold green]Usuário SSH[/bold green]", default="admin")
                pwd = Prompt.ask("[bold green]Senha SSH[/bold green]", password=True)
                targets = [OltTarget(host=host, username=user, password=pwd, slot=args.slot, port_gpon=args.port_gpon)]
        else:
            targets = [
                OltTarget(
                    host=args.host,
                    port=args.port or 22,
                    username=args.user or "admin",
                    password=args.password or "",
                    slot=args.slot,
                    port_gpon=args.port_gpon,
                )
            ]
    else:
        # Demo / Mock Target
        targets = [
            OltTarget(
                host="192.168.1.100",
                port=22,
                username="admin",
                password="mock",
                name="OLT Central (Simulada)",
                slot=args.slot or 1,
                port_gpon=args.port_gpon or 0,
            )
        ]

    # Show configuration summary
    mode_badge = (
        "[bold yellow][MODO SIMULACAO / DRY-RUN][/bold yellow]"
        if cleanup_settings.dry_run
        else "[bold red][MODO REAL - EXECUCAO ATIVA][/bold red]"
    )
    mock_badge = " [bold magenta][DEMO / MOCK][/bold magenta]" if is_mock else ""

    summary_panel = f"""
    [bold]Status de Execução:[/bold] {mode_badge} {mock_badge}
    [bold]Total de OLTs Alvo:[/bold] {len(targets)}
    [bold]Limite Offline:[/bold] >= {int(cleanup_settings.time_value)} {cleanup_settings.time_unit} ({int(cleanup_settings.days_offline_threshold)} dias)
    [bold]Whitelist (Protegidos):[/bold] {', '.join(cleanup_settings.description_whitelist)}
    [bold]Diretório de Backup / Rollback:[/bold] {cleanup_settings.backup_dir}
    """
    console.print(Panel(summary_panel, title="[bold]Parâmetros da Operação[/bold]", border_style="blue"))

    use_js = args.jumpserver
    js_host = args.js_host
    js_user = args.js_user
    js_pass = args.js_pass

    if use_js and not is_mock:
        if not js_host:
            js_host = Prompt.ask("[bold green]Host do JumpServer (ex: jump.provedor.com.br)[/bold green]")
        if not js_user:
            js_user = Prompt.ask("[bold green]Seu Usuário do JumpServer[/bold green]")
        if not js_pass:
            js_pass = Prompt.ask("[bold green]Sua Senha do JumpServer[/bold green]", password=True)

    # Scanning Phase
    coordinator = MultiOltCoordinator(
        backup_mgr,
        cleanup_settings,
        is_demo=is_mock,
        use_jumpserver=use_js,
        jumpserver_host=js_host or "",
        jumpserver_port=args.js_port,
        jumpserver_user=js_user or "",
        jumpserver_password=js_pass or "",
        jumpserver_account=args.js_account or "",
    )
    candidates: List[CleanupCandidate] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        scan_task = progress.add_task("Varrendo OLTs...", total=None)

        def update_progress(msg: str):
            progress.update(scan_task, description=msg)

        candidates = coordinator.scan_all(targets, progress_callback=update_progress)

    if not candidates:
        console.print("[yellow]Nenhuma ONT offline encontrada nas OLTs analisadas.[/yellow]")
        return

    # Display Candidates Table
    display_candidates_table(candidates)

    eligible_candidates = [c for c in candidates if c.eligible_for_deletion]
    total_eligible = len(eligible_candidates)

    console.print(
        f"\n[bold]Total escaneado:[/bold] {len(candidates)} ONTs offline | "
        f"[bold red]Elegíveis para exclusão:[/bold red] {total_eligible}"
    )

    if total_eligible == 0:
        console.print("[green]Nenhuma ONT atingiu os critérios para exclusão. Operação finalizada![/green]")
        return

    # Customer Contract Review Step
    if not args.auto_confirm:
        console.print(
            "\n[bold yellow]CONFERÊNCIA DE CONTRATOS DOS CLIENTES:[/bold yellow]\n"
            "Verifique os logins listados acima no seu ERP/sistema para confirmar se o contrato está inativo."
        )
        uncheck_choice = Confirm.ask(
            "Deseja desmarcar algum cliente específico da lista antes de prosseguir?",
            default=False,
        )
        if uncheck_choice:
            items_to_skip = Prompt.ask(
                "Digite os números dos itens que deseja DESMARCAR (separados por vírgula, ex: 1, 3)"
            )
            for raw_num in items_to_skip.split(","):
                cleaned = raw_num.strip()
                if cleaned.isdigit():
                    idx = int(cleaned) - 1
                    if 0 <= idx < len(candidates):
                        candidates[idx].selected_by_user = False
                        console.print(
                            f"[yellow]Item {cleaned} ({candidates[idx].ont.customer_login}) desmarcado.[/yellow]"
                        )

    # Filter confirmed candidates
    confirmed_candidates = [
        c for c in candidates if c.eligible_for_deletion and c.selected_by_user
    ]
    total_confirmed = len(confirmed_candidates)

    if total_confirmed == 0:
        console.print("[yellow]Nenhuma ONT permaneceu selecionada para exclusão. Finalizando.[/yellow]")
        return

    # Confirmation Prompt
    if not args.auto_confirm:
        if cleanup_settings.dry_run:
            proceed = Confirm.ask(
                f"\n[bold yellow]Deseja executar a SIMULAÇÃO de limpeza das {total_confirmed} ONTs confirmadas?[/bold yellow]",
                default=True,
            )
        else:
            console.print(
                Panel(
                    "[bold red]ATENÇÃO: VOCÊ ESTÁ EM MODO REAL![/bold red]\n"
                    f"As {total_confirmed} ONTs confirmadas e todos os seus service-ports serão DELETADOS da OLT!\n"
                    "Backups e scripts individuais de Rollback serão gerados antes da remoção.",
                    border_style="red",
                )
            )
            confirm_input = Prompt.ask(
                "[bold red]Digite 'DELETAR' para confirmar a limpeza permanente[/bold red]"
            )
            proceed = confirm_input.strip() == "DELETAR"

        if not proceed:
            console.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
            return

    # Execution Phase
    console.print("\n[bold cyan]Iniciando rotina de limpeza...[/bold cyan]\n")

    targets_by_ip = {t.host: t for t in targets}
    results = coordinator.execute_cleanups(targets_by_ip, candidates)

    for i, res in enumerate(results, 1):
        ont = res.ont
        console.print(
            f"[[bold cyan]{i}/{len(results)}[/bold cyan]] Processando ONT [bold]{ont.location}[/bold] "
            f"(OLT: {ont.olt_host}, Login: {ont.display_login}, SN: {ont.sn})..."
        )
        if res.service_ports_deleted:
            console.print(
                f"   [dim]-> undo service-port(s): {res.service_ports_deleted}[/dim]"
            )
        console.print(f"   [dim]-> interface gpon 0/{ont.slot}[/dim]")
        console.print(f"   [dim]-> ont delete {ont.port} {ont.ont_id}[/dim]")

        if res.success:
            console.print(f"   [bold green][OK] Sucesso:[/bold green] ONT {ont.location} removida com sucesso!")
            if res.rollback_file:
                console.print(f"     [dim]Rollback individual: {res.rollback_file}[/dim]")
        else:
            console.print(f"   [bold red][FALHA] Erro:[/bold red] {res.error_message}")

    # Generate Audit and Reports
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_report = os.path.join(cleanup_settings.backup_dir, f"relatorio_limpeza_{timestamp}.xlsx")
    export_cleanup_report_excel(results, excel_report)

    session_rollback = backup_mgr.save_session_rollback(confirmed_candidates)
    audit_json = backup_mgr.save_audit_report(results)

    # Final Summary Panel
    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count

    final_text = f"""
    [bold green]Concluído com sucesso:[/bold green] {success_count} ONTs
    [bold red]Falhas:[/bold red] {failed_count}
    [bold]Relatório em Excel:[/bold] [cyan]{excel_report}[/cyan]
    [bold]Script de Rollback da Sessão:[/bold] [cyan]{session_rollback or 'N/A'}[/cyan]
    [bold]Relatório JSON:[/bold] [cyan]{audit_json}[/cyan]
    """
    console.print(Panel(final_text, title="[bold]Resumo Final da Operação[/bold]", border_style="green"))
