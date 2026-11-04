import os
import sys
import logging
from pathlib import Path

# Garante que a raiz do projeto esteja no sys.path mesmo se o Streamlit for executado de dentro da pasta app/
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

LOG_FILE_PATH = ROOT_DIR / "logs" / "limpa_hw.log"


def configure_web_logging() -> None:
    """Garante que todas as operações da interface Web sejam gravadas em logs/limpa_hw.log."""
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    app_logger = logging.getLogger("limpahw")
    app_logger.setLevel(logging.DEBUG)
    has_file_handler = any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == LOG_FILE_PATH.resolve()
        for h in app_logger.handlers
    )
    if not has_file_handler:
        fh = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        app_logger.addHandler(fh)


configure_web_logging()

import streamlit as st
import pandas as pd
from datetime import datetime

import importlib

try:
    from app.config import CleanupSettings, update_env_file
except ImportError:
    # Se o processo do Streamlit estava aberto em segundo plano com cache antigo na memória, força o recarregamento
    import app.config
    importlib.reload(app.config)
    from app.config import CleanupSettings, update_env_file

from app.models import OltTarget, CleanupCandidate, CleanupResult
from app.excel_manager import (
    create_template_excel,
    read_olts_from_excel,
    export_cleanup_report_excel,
)
from app.backup_manager import BackupManager
from app.cleaner import MultiOltCoordinator, OntCleaner

st.set_page_config(
    page_title="LimpaHW - Limpeza de ONTs Huawei",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown(
    """
    <style>
    /* Oculta completamente vestígios do Streamlit (Botão Deploy, menu três pontos, cabeçalho e rodapé) */
    #MainMenu {visibility: hidden !important; display: none !important;}
    footer {visibility: hidden !important; display: none !important;}
    header[data-testid="stHeader"] {visibility: hidden !important; height: 0 !important; margin: 0 !important; padding: 0 !important;}
    .stDeployButton {display: none !important; visibility: hidden !important;}
    [data-testid="stToolbar"] {display: none !important; visibility: hidden !important;}
    [data-testid="stToolbarActions"] {display: none !important; visibility: hidden !important;}
    [data-testid="stDecoration"] {display: none !important; visibility: hidden !important;}
    [data-testid="stStatusWidget"] {display: none !important; visibility: hidden !important;}
    .stAppToolbar {display: none !important; visibility: hidden !important;}

    /* Reduz o espaçamento superior deixado pelo cabeçalho oculto */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
    }

    .main-header {
        font-size: 26px;
        font-weight: 700;
        color: #1F497D;
        margin-bottom: 5px;
    }
    .sub-header {
        font-size: 14px;
        color: #555555;
        margin-bottom: 20px;
    }
    .stButton>button {
        border-radius: 6px;
        font-weight: 600;
    }
    .metric-box {
        background-color: #f8f9fa;
        border-left: 4px solid #1F497D;
        padding: 12px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize Session State
if "candidates" not in st.session_state:
    st.session_state.candidates = []
if "targets" not in st.session_state:
    st.session_state.targets = []
if "results" not in st.session_state:
    st.session_state.results = []
if "excel_report_path" not in st.session_state:
    st.session_state.excel_report_path = None
if "session_rollback_path" not in st.session_state:
    st.session_state.session_rollback_path = None
if "scan_errors" not in st.session_state:
    st.session_state.scan_errors = []
if "rollback_status" not in st.session_state:
    st.session_state.rollback_status = {}


def get_template_bytes():
    template_path = "modelo_olts.xlsx"
    if not os.path.exists(template_path):
        create_template_excel(template_path)
    with open(template_path, "rb") as f:
        return f.read()


# Sidebar: Configuration
st.sidebar.image("https://img.icons8.com/color/96/router.png", width=64)
st.sidebar.title("Configurações")

# 1. Period Limit
st.sidebar.subheader("1. Limite Offline")
saved_time_val = int(float(os.getenv("CLEANUP_TIME_VALUE", "30")))
saved_time_unit = os.getenv("CLEANUP_TIME_UNIT", "Dias").capitalize()
unit_options = ["Dias", "Semanas", "Meses"]
unit_idx = unit_options.index(saved_time_unit) if saved_time_unit in unit_options else 0

col_val, col_unit = st.sidebar.columns([1, 1])
with col_val:
    time_val = st.number_input("Quantidade", min_value=1, value=saved_time_val, step=1, format="%d")
with col_unit:
    time_unit = st.selectbox("Unidade", unit_options, index=unit_idx)

cleanup_settings = CleanupSettings()
cleanup_settings.set_time_threshold(int(time_val), time_unit)

st.sidebar.info(
    f"⏱️ **Critério:** Equipamentos offline há **>= {int(cleanup_settings.days_offline_threshold)} dias**"
)

# 2. Connection Method
st.sidebar.subheader("2. Acesso à Rede")
saved_js_enabled = os.getenv("JUMPSERVER_ENABLED", "true").lower() in ("true", "1", "yes")
conn_default_idx = 0 if saved_js_enabled else 1

conn_method = st.sidebar.radio(
    "Método de Conexão",
    ["JumpServer (Bastion / PAM)", "SSH Direto nas OLTs", "Modo Demonstração (Simulado)"],
    index=conn_default_idx,
    help="O modo JumpServer conecta via Bastion SSH usando seu login corporativo, sem precisar de senhas de OLT na planilha.",
)

use_jumpserver = conn_method.startswith("JumpServer")
is_demo = conn_method.startswith("Modo Demonstração")

js_host = os.getenv("JUMPSERVER_HOST", "")
js_port = int(os.getenv("JUMPSERVER_PORT", "2222"))
js_user = os.getenv("JUMPSERVER_USER", "")
js_pass = os.getenv("JUMPSERVER_PASSWORD", "")
js_account = os.getenv("JUMPSERVER_ACCOUNT", "")

direct_user = os.getenv("OLT_USERNAME", "admin")
direct_pass = os.getenv("OLT_PASSWORD", "")
direct_port = int(os.getenv("OLT_PORT", "22"))
direct_timeout = int(os.getenv("OLT_TIMEOUT", "30"))

save_js_clicked = False
save_direct_clicked = False

if use_jumpserver:
    st.sidebar.markdown("**Acesso JumpServer / Bastion:**")
    js_host = st.sidebar.text_input(
        "Host do JumpServer",
        value=js_host,
        placeholder="ex: jump.seuprovedor.com.br",
        help="Endereço IP ou hostname do servidor JumpServer / Bastion.",
    )
    col_u, col_p = st.sidebar.columns([1, 1])
    with col_u:
        js_user = st.sidebar.text_input(
            "Usuário",
            value=js_user,
            placeholder="ex: seu.login",
            help="Seu login pessoal de acesso ao JumpServer",
        )
    with col_p:
        js_pass = st.sidebar.text_input(
            "Senha",
            type="password",
            value=js_pass,
            help="Sua senha de acesso ao JumpServer",
        )
    with st.sidebar.expander("⚙️ Opções Avançadas do JumpServer", expanded=False):
        js_port = st.number_input("Porta SSH Bastion", value=js_port, step=1)
        js_account = st.text_input(
            "Conta de Sistema da OLT",
            value=js_account,
            placeholder="ex: auth.olt (opcional)",
            help="Nome da conta cadastrada no cofre do JumpServer para as OLTs (opcional se houver apenas uma cadastrada)",
        )
    st.sidebar.caption("🔒 As credenciais da OLT são injetadas pelo JumpServer de forma segura.")
    save_js_clicked = st.sidebar.button("💾 Salvar Configurações (JumpServer)", use_container_width=True)
    save_msg_placeholder = st.sidebar.empty()

elif not is_demo:
    st.sidebar.markdown("**Credenciais SSH Direto da OLT:**")
    direct_user = st.sidebar.text_input(
        "Usuário Padrão",
        value=direct_user,
        placeholder="ex: admin",
        help="Usuário padrão caso não seja especificado na planilha Excel",
    )
    direct_pass = st.sidebar.text_input(
        "Senha Padrão",
        type="password",
        value=direct_pass,
        help="Senha padrão caso não seja especificada na planilha Excel",
    )
    with st.sidebar.expander("⚙️ Opções Avançadas SSH", expanded=False):
        direct_port = st.number_input("Porta SSH", value=direct_port, step=1)
        direct_timeout = st.number_input("Timeout (seg)", value=direct_timeout, step=5)

    save_direct_clicked = st.sidebar.button("💾 Salvar Configurações (SSH Direto)", use_container_width=True)
    save_msg_placeholder = st.sidebar.empty()

# 3. Safety & Whitelist
st.sidebar.subheader("3. Segurança")
mode_choice = st.sidebar.radio(
    "Modo de Operação",
    ["Simulação (Dry-Run)", "Deleção Real (OLT)"],
    index=0,
    help="No modo simulação, o sistema varre e simula todas as etapas sem enviar comandos destrutivos à OLT.",
)
cleanup_settings.dry_run = mode_choice.startswith("Simulação")

del_never = st.sidebar.checkbox(
    "Deletar ONTs que nunca subiram",
    value=os.getenv("CLEANUP_DELETE_NEVER_CONNECTED", "false").lower() in ("true", "1", "yes"),
    help="Se ativado, inclui ONTs provisionadas que nunca tiveram primeiro registro.",
)
cleanup_settings.delete_never_connected = del_never

whitelist_input = st.sidebar.text_input(
    "Whitelist (palavras protegidas)",
    value=os.getenv("CLEANUP_WHITELIST", "VIP, DEDICADO, GOVERNO, EMPRESA, TESTE"),
    help="Separe por vírgula termos que impeçam a deleção caso estejam na descrição da ONT.",
)
cleanup_settings.description_whitelist = [
    w.strip().upper() for w in whitelist_input.split(",") if w.strip()
]

# Handle Save Buttons after all sidebar inputs are defined
if save_js_clicked:
    updates = {
        "JUMPSERVER_ENABLED": "true",
        "JUMPSERVER_HOST": js_host.strip(),
        "JUMPSERVER_PORT": str(int(js_port)),
        "JUMPSERVER_USER": js_user.strip(),
        "JUMPSERVER_PASSWORD": js_pass,
        "JUMPSERVER_ACCOUNT": js_account.strip(),
        "CLEANUP_TIME_VALUE": str(int(time_val)),
        "CLEANUP_TIME_UNIT": time_unit,
        "CLEANUP_DELETE_NEVER_CONNECTED": str(del_never).lower(),
        "CLEANUP_WHITELIST": whitelist_input,
    }
    update_env_file(updates)
    save_msg_placeholder.success("✅ Configurações salvas no arquivo .env!")

if save_direct_clicked:
    updates = {
        "JUMPSERVER_ENABLED": "false",
        "OLT_USERNAME": direct_user.strip(),
        "OLT_PASSWORD": direct_pass,
        "OLT_PORT": str(int(direct_port)),
        "OLT_TIMEOUT": str(int(direct_timeout)),
        "CLEANUP_TIME_VALUE": str(int(time_val)),
        "CLEANUP_TIME_UNIT": time_unit,
        "CLEANUP_DELETE_NEVER_CONNECTED": str(del_never).lower(),
        "CLEANUP_WHITELIST": whitelist_input,
    }
    update_env_file(updates)
    save_msg_placeholder.success("✅ Configurações salvas no arquivo .env!")


# Main Area
st.markdown('<div class="main-header">LimpaHW - Limpeza de ONTs Offline Huawei GPON</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Varredura de OLTs via planilha Excel, validação de login/contrato do cliente e exclusão segura com Rollback.</div>',
    unsafe_allow_html=True,
)

tab_scan, tab_review, tab_results, tab_logs = st.tabs(
    [
        "1. Importar & Varrer",
        "2. Revisar Clientes & Confirmar",
        "3. Relatório & Rollback",
        "4. Logs de Diagnóstico",
    ]
)

# ---------------- TAB 1: Importar & Varrer ----------------
with tab_scan:
    st.subheader("Importação da Planilha de OLTs")

    c1, c2 = st.columns([2, 1])
    with c1:
        uploaded_file = st.file_uploader(
            "Selecione a planilha Excel com as OLTs (.xlsx)",
            type=["xlsx"],
            help="A planilha deve conter as colunas: IP_OLT, Usuario, Senha (e opcionais: Nome_OLT, Porta_SSH, Slot, Porta_PON).",
        )
    with c2:
        st.write("**Planilha Modelo:**")
        st.download_button(
            label="📥 Baixar Planilha Modelo (.xlsx)",
            data=get_template_bytes(),
            file_name="modelo_olts.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    if uploaded_file:
        try:
            targets = read_olts_from_excel(uploaded_file)

            # Preenche credenciais padrão se estiver em SSH Direto e campos vazios
            if not use_jumpserver and not is_demo:
                for t in targets:
                    if not t.username and direct_user:
                        t.username = direct_user
                    if not t.password and direct_pass:
                        t.password = direct_pass
                    if (not t.port or t.port == 22) and direct_port:
                        t.port = int(direct_port)

            st.session_state.targets = targets
            st.success(f"Planilha carregada com sucesso! **{len(targets)} OLT(s)** identificada(s).")

            # Show targets table
            target_df = pd.DataFrame(
                [
                    {
                        "Nome": t.name or "-",
                        "IP OLT": t.host,
                        "Porta": t.port,
                        "Usuário": t.username,
                        "Slot Filtrado": t.slot if t.slot is not None else "Todos",
                        "Porta PON Filtrada": t.port_gpon if t.port_gpon is not None else "Todas",
                    }
                    for t in targets
                ]
            )
            st.dataframe(target_df, use_container_width=True, hide_index=True)

            if st.button("🚀 Iniciar Varredura de ONTs Offline", type="primary", use_container_width=True):
                if use_jumpserver and not is_demo and (not js_host or not js_user or not js_pass):
                    st.error("⚠️ Informe o Host, Usuário e Senha do JumpServer na barra lateral esquerda antes de iniciar a varredura.")
                else:
                    backup_mgr = BackupManager(cleanup_settings.backup_dir)
                    coordinator = MultiOltCoordinator(
                        backup_manager=backup_mgr,
                        settings=cleanup_settings,
                        is_demo=is_demo,
                        use_jumpserver=use_jumpserver,
                        jumpserver_host=js_host,
                        jumpserver_port=int(js_port),
                        jumpserver_user=js_user,
                        jumpserver_password=js_pass,
                        jumpserver_account=js_account,
                    )

                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    def update_progress(msg: str):
                        status_text.text(msg)

                    candidates = coordinator.scan_all(targets, progress_callback=update_progress)
                    st.session_state.candidates = candidates
                    st.session_state.scan_errors = coordinator.scan_errors
                    progress_bar.progress(100)
                    status_text.text("Varredura concluída!")

                    if coordinator.scan_errors:
                        for err in coordinator.scan_errors:
                            st.error(f"❌ {err}")
                        st.warning(
                            "💡 Consulte a aba **'4. Logs de Diagnóstico'** (ou o arquivo `logs/limpa_hw.log`) para ver o retorno detalhado do JumpServer/OLT."
                        )

                    if candidates:
                        st.success(f"Varredura finalizada! **{len(candidates)} ONTs offline** encontradas.")
                        st.info("Acesse a aba **'2. Revisar Clientes & Confirmar'** para verificar os logins e aprovar as deleções.")
                    elif not coordinator.scan_errors:
                        st.warning(
                            "⚠️ A varredura terminou sem erros de conexão, mas **0 ONTs offline** atenderam aos critérios informados. "
                            "Verifique a aba **'4. Logs de Diagnóstico'** para conferir os comandos executados na OLT."
                        )

        except Exception as e:
            st.error(f"Erro ao processar a planilha: {e}")

    elif is_demo:
        st.warning("⚠️ Você ativou o Modo Demonstração (OLT Simulada). Você pode clicar abaixo para testar sem planilha real:")
        if st.button("🧪 Executar Varredura com OLT de Teste"):
            mock_target = OltTarget(
                host="192.168.1.100",
                port=22,
                username="admin",
                password="mock",
                name="OLT Central (Simulada)",
                slot=1,
                port_gpon=0,
            )
            st.session_state.targets = [mock_target]
            backup_mgr = BackupManager(cleanup_settings.backup_dir)
            coordinator = MultiOltCoordinator(backup_mgr, cleanup_settings, is_demo=True)
            candidates = coordinator.scan_all([mock_target])
            st.session_state.candidates = candidates
            st.success(f"Varredura simulada concluída! **{len(candidates)} ONTs offline** encontradas.")
            st.info("Acesse a aba **'2. Revisar Clientes & Confirmar'** para prosseguir.")


# ---------------- TAB 2: Revisar Clientes & Confirmar ----------------
with tab_review:
    st.subheader("Conferência de Clientes / Logins e Seleção para Limpeza")

    if not st.session_state.candidates:
        st.info("Nenhuma varredura realizada ainda. Importe a planilha e inicie a varredura na aba anterior.")
    else:
        candidates = st.session_state.candidates
        eval_cleaner = OntCleaner(client=None, backup_manager=None, settings=cleanup_settings)

        # Prepare DataFrame for data_editor with ONLY candidates that match the current filter criteria
        data_rows = []
        for i, c in enumerate(candidates):
            ont = c.ont
            is_elig, reason = eval_cleaner.evaluate_eligibility(ont)
            c.eligible_for_deletion = is_elig
            if not is_elig:
                c.selected_by_user = False
                continue

            data_rows.append(
                {
                    "Deletar": c.selected_by_user,
                    "OLT": f"{ont.olt_name} ({ont.olt_host})" if ont.olt_name else ont.olt_host,
                    "Local": ont.location,
                    "Login / Descrição (ERP)": ont.customer_login or ont.description or "-",
                    "Serial (SN)": ont.sn or "-",
                    "MAC": ont.mac or "-",
                    "Dias Offline": f"{int(ont.calculated_offline_days)} dias" if ont.calculated_offline_days is not None else "Nunca",
                    "Service-Ports": ", ".join(map(str, c.service_ports)) if c.service_ports else "-",
                    "Motivo Queda": ont.last_down_cause or "-",
                    "Status": "Elegível",
                    "_idx": i,
                }
            )

        if not data_rows:
            st.warning(
                f"Nenhuma ONT da varredura atende ao filtro atual (>= {int(cleanup_settings.days_offline_threshold)} dias offline)."
            )
        else:
            st.markdown(
                """
                > **Atenção:** Verifique abaixo o **Login / Descrição** de cada cliente no seu ERP para confirmar se o contrato 
                > está realmente inativo. Desmarque as caixas de seleção daquelas que **NÃO** devem ser deletadas.
                """
            )

            df_editor = pd.DataFrame(data_rows)

            edited_df = st.data_editor(
                df_editor,
                column_config={
                    "Deletar": st.column_config.CheckboxColumn(
                        "Confirmar Deleção?",
                        help="Marque para confirmar a exclusão deste equipamento",
                        default=False,
                    ),
                    "_idx": None,  # Hide internal index
                },
                disabled=[
                    "OLT",
                    "Local",
                    "Login / Descrição (ERP)",
                    "Serial (SN)",
                    "MAC",
                    "Dias Offline",
                    "Service-Ports",
                    "Motivo Queda",
                    "Status",
                ],
                use_container_width=True,
                hide_index=True,
            )

            # Update candidate selections based on edited_df
            selected_count = 0
            for _, row in edited_df.iterrows():
                idx = int(row["_idx"])
                is_checked = bool(row["Deletar"])
                candidates[idx].selected_by_user = is_checked
                if is_checked:
                    selected_count += 1

            col_m1, col_m2 = st.columns(2)
            col_m1.metric(
                f"Encontradas no Filtro (>= {int(cleanup_settings.days_offline_threshold)} dias)",
                len(data_rows),
            )
            col_m2.metric("Confirmadas p/ Exclusão", selected_count)

            st.divider()

            if selected_count == 0:
                st.warning("Nenhuma ONT selecionada para limpeza.")
            else:
                if cleanup_settings.dry_run:
                    btn_label = f"🟡 Executar SIMULAÇÃO de Limpeza ({selected_count} ONTs)"
                    btn_type = "primary"
                else:
                    btn_label = f"🔴 EXECUTAR LIMPEZA REAL NA OLT ({selected_count} ONTs)"
                    btn_type = "primary"
                    st.error("⚠️ MODO REAL ATIVADO: As ONTs selecionadas e seus service-ports serão excluídos da OLT!")

                if st.button(btn_label, type=btn_type, use_container_width=True):
                    backup_mgr = BackupManager(cleanup_settings.backup_dir)
                    coordinator = MultiOltCoordinator(
                        backup_manager=backup_mgr,
                        settings=cleanup_settings,
                        is_demo=is_demo,
                        use_jumpserver=use_jumpserver,
                        jumpserver_host=js_host,
                        jumpserver_port=int(js_port),
                        jumpserver_user=js_user,
                        jumpserver_password=js_pass,
                        jumpserver_account=js_account,
                    )

                    targets_by_ip = {t.host: t for t in st.session_state.targets}

                    exec_status = st.empty()
                    exec_progress = st.progress(0)

                    def update_exec(msg: str):
                        exec_status.text(msg)

                    results = coordinator.execute_cleanups(
                        targets_by_ip=targets_by_ip,
                        candidates=candidates,
                        progress_callback=update_exec,
                    )
                    st.session_state.results = results
                    exec_progress.progress(100)

                    # Generate reports
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    excel_report_file = os.path.join(
                        cleanup_settings.backup_dir, f"relatorio_limpeza_{timestamp}.xlsx"
                    )
                    export_cleanup_report_excel(results, excel_report_file)
                    st.session_state.excel_report_path = excel_report_file

                    session_rollback = backup_mgr.save_session_rollback(
                        [c for c in candidates if c.selected_by_user and c.eligible_for_deletion]
                    )
                    st.session_state.session_rollback_path = session_rollback

                    st.success(f"Rotina de limpeza concluída! {sum(1 for r in results if r.success)} ONTs processadas.")
                    st.info("Acesse a aba **'3. Relatório & Rollback'** para baixar o relatório em Excel e os arquivos de Rollback.")


# ---------------- TAB 3: Relatório & Rollback ----------------
with tab_results:
    st.subheader("Relatório da Execução e Scripts de Rollback Individual")

    if not st.session_state.results:
        st.info("Nenhuma execução realizada até o momento.")
    else:
        results = st.session_state.results
        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        r_col1, r_col2 = st.columns([1, 1])
        with r_col1:
            if st.session_state.excel_report_path and os.path.exists(st.session_state.excel_report_path):
                with open(st.session_state.excel_report_path, "rb") as f:
                    st.download_button(
                        label="📊 Baixar Relatório Completo em Excel (.xlsx)",
                        data=f.read(),
                        file_name=os.path.basename(st.session_state.excel_report_path),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
        with r_col2:
            if st.session_state.session_rollback_path and os.path.exists(st.session_state.session_rollback_path):
                with open(st.session_state.session_rollback_path, "rb") as f:
                    st.download_button(
                        label="📜 Baixar Script de Rollback da Sessão Completa (.txt)",
                        data=f.read(),
                        file_name=os.path.basename(st.session_state.session_rollback_path),
                        mime="text/plain",
                        use_container_width=True,
                    )

        st.write("---")
        st.write("### Equipamentos Processados e Opções de Rollback Individual:")

        targets_by_ip = {t.host: t for t in st.session_state.targets}

        for idx_res, res in enumerate(results):
            ont = res.ont
            rb_key = f"{ont.olt_host}_{ont.location}_{idx_res}"
            already_restored = st.session_state.rollback_status.get(rb_key, {}).get("ok", False)
            status_icon = "🔄 Reincluído" if already_restored else ("✅ Removido" if res.success else "❌ Falha")

            with st.expander(
                f"{status_icon} | ONT {ont.location} | Cliente: {ont.customer_login or ont.description or '-'} | SN: {ont.sn}",
                expanded=True,
            ):
                info_c1, info_c2 = st.columns([1, 1])
                with info_c1:
                    st.write(f"**OLT:** {ont.olt_name or '-'} (`{ont.olt_host}`)")
                    st.write(f"**Localização GPON:** `{ont.location}`")
                    st.write(f"**Login / Descrição:** `{ont.customer_login or ont.description or '-'}`")
                with info_c2:
                    st.write(f"**Serial (SN):** `{ont.sn or '-'}` | **MAC:** `{ont.mac or '-'}`")
                    st.write(f"**Service-Ports Removidos:** `{res.service_ports_deleted}`")
                    dias_off_str = f"{int(ont.calculated_offline_days)} dias" if ont.calculated_offline_days is not None else "Nunca"
                    st.write(f"**Tempo Offline:** {dias_off_str}")

                if res.rollback_commands:
                    with st.expander("📄 Ver Comandos CLI de Restauração", expanded=False):
                        st.code("\n".join(res.rollback_commands), language="bash")

                    btn_col1, btn_col2 = st.columns([1, 1])
                    with btn_col1:
                        exec_rb_clicked = st.button(
                            f"🔄 Reincluir Equipamento na OLT ({ont.location})",
                            key=f"exec_rb_{rb_key}",
                            type="primary",
                            use_container_width=True,
                            help="Conecta na OLT agora e executa automaticamente os comandos de rollback para readicionar esta ONT e seus service-ports.",
                        )
                    with btn_col2:
                        if res.rollback_file and os.path.exists(res.rollback_file):
                            with open(res.rollback_file, "r", encoding="utf-8") as f:
                                st.download_button(
                                    label=f"📥 Baixar Script de Rollback ({ont.location})",
                                    data=f.read(),
                                    file_name=os.path.basename(res.rollback_file),
                                    key=f"dl_rb_{rb_key}",
                                    use_container_width=True,
                                )

                    if exec_rb_clicked:
                        target_olt = targets_by_ip.get(ont.olt_host) or OltTarget(
                            host=ont.olt_host or "",
                            name=ont.olt_name or "",
                            port=int(direct_port),
                            username=direct_user,
                            password=direct_pass,
                        )
                        backup_mgr = BackupManager(cleanup_settings.backup_dir)
                        rb_coordinator = MultiOltCoordinator(
                            backup_manager=backup_mgr,
                            settings=cleanup_settings,
                            is_demo=is_demo,
                            use_jumpserver=use_jumpserver,
                            jumpserver_host=js_host,
                            jumpserver_port=int(js_port),
                            jumpserver_user=js_user,
                            jumpserver_password=js_pass,
                            jumpserver_account=js_account,
                        )
                        rb_status_box = st.empty()
                        with st.spinner(f"Reincluindo ONT {ont.location} na OLT {target_olt.display_name}..."):
                            ok_rb, msg_rb = rb_coordinator.execute_rollback(
                                target=target_olt,
                                rollback_commands=res.rollback_commands,
                                progress_callback=lambda m: rb_status_box.text(m),
                            )
                        rb_status_box.empty()
                        st.session_state.rollback_status[rb_key] = {"ok": ok_rb, "msg": msg_rb}

                    if rb_key in st.session_state.rollback_status:
                        st_info = st.session_state.rollback_status[rb_key]
                        if st_info["ok"]:
                            st.success(f"✅ Rollback concluído para ONT {ont.location}!")
                        else:
                            st.error(f"❌ Falha no Rollback da ONT {ont.location}:")
                        st.code(st_info["msg"], language="text")


# ---------------- TAB 4: Logs de Diagnóstico ----------------
with tab_logs:
    st.subheader("📋 Logs de Diagnóstico e Auditoria")
    st.caption(f"Caminho do arquivo de log no disco: `{LOG_FILE_PATH}`")

    col_l1, col_l2, col_l3, col_l4 = st.columns([1, 1, 1, 1])
    with col_l1:
        max_lines = st.selectbox("Exibir últimas linhas", [100, 250, 500, 1000], index=1)
    with col_l2:
        level_filter = st.selectbox("Filtrar por Nível", ["TODOS", "DEBUG", "INFO", "WARNING", "ERROR"], index=0)
    with col_l3:
        st.write("")
        refresh_logs = st.button("🔄 Atualizar Logs", use_container_width=True)
    with col_l4:
        st.write("")
        if st.button("🗑️ Limpar Arquivo de Log", use_container_width=True):
            if LOG_FILE_PATH.exists():
                LOG_FILE_PATH.write_text("", encoding="utf-8")
                st.success("Arquivo de log limpo!")

    if LOG_FILE_PATH.exists():
        try:
            raw_content = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace")
            lines = raw_content.splitlines()
            if level_filter != "TODOS":
                lines = [ln for ln in lines if f"| {level_filter}" in ln]
            tail_lines = lines[-int(max_lines):]
            display_text = "\n".join(tail_lines) if tail_lines else "(Nenhum registro encontrado para o filtro selecionado)"
            st.code(display_text, language="text")

            st.download_button(
                label="📥 Baixar Arquivo Completo (limpa_hw.log)",
                data=raw_content.encode("utf-8"),
                file_name="limpa_hw.log",
                mime="text/plain",
            )
        except Exception as e:
            st.error(f"Erro ao ler arquivo de log: {e}")
    else:
        st.info("O arquivo de log ainda não foi criado.")


