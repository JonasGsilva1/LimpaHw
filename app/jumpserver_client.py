import time
import re
import logging
from typing import Optional, Tuple
import paramiko

logger = logging.getLogger("limpahw.jumpserver")

# Regex to strip ANSI escape codes (colors, cursor positions, terminal title, etc.)
ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)")
BACKSPACE_REGEX = re.compile(r".\x08")


def strip_ansi(text: str) -> str:
    """Removes all ANSI escape sequences and control characters from terminal output."""
    clean = ANSI_REGEX.sub("", text)
    # Handle backspaces used by Huawei OLT to erase '--More--' prompts
    while BACKSPACE_REGEX.search(clean):
        clean = BACKSPACE_REGEX.sub("", clean)
    clean = clean.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return clean


class JumpServerOltClient:
    """
    SSH Client that connects to Huawei OLTs through an open-source Bastion/PAM host (JumpServer).
    Uses the technician's personal corporate credentials and delegates OLT authentication
    to JumpServer's system account configured in the vault.
    """

    def __init__(
        self,
        jumpserver_host: str = "",
        jumpserver_port: int = 2222,
        username: str = "",
        password: str = "",
        system_user: str = "",
        target_olt: str = "",  # IP or Asset Name
        fallback_target: str = "",
        timeout: int = 30,
    ):
        self.jumpserver_host = jumpserver_host.strip()
        self.jumpserver_port = int(jumpserver_port)
        self.username = username.strip()
        self.password = password
        self.system_user = system_user.strip()
        self.target_olt = target_olt.strip()
        self.fallback_target = fallback_target.strip()
        self.timeout = timeout
        self.ssh: Optional[paramiko.SSHClient] = None
        self.channel: Optional[paramiko.Channel] = None
        self._current_interface: Optional[str] = None

    def connect(self) -> None:
        """
        Connects to JumpServer, selects the target OLT, and establishes Huawei OLT CLI session.
        """
        logger.info(
            f"[JumpServer] Conectando a {self.jumpserver_host}:{self.jumpserver_port} (usuário: '{self.username}')..."
        )

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self.ssh.connect(
                hostname=self.jumpserver_host,
                port=self.jumpserver_port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as e:
            logger.error(f"[JumpServer] Falha na autenticação SSH: {e}")
            raise ConnectionError(f"Falha na autenticação com o JumpServer ({self.jumpserver_host}): {e}")

        # Open pseudo-terminal channel (required by JumpServer Koko)
        self.channel = self.ssh.invoke_shell(term="vt100", width=250, height=100)
        self.channel.settimeout(float(self.timeout))

        # 1. Wait specifically for JumpServer Koko menu prompt ([Host]> or similar)
        initial_banner = self._read_until(["[Host]>", "Host>", "Opt>"], timeout=12)
        logger.info(f"[JumpServer] Menu inicial carregado. Fim do banner:\n{initial_banner[-300:]}")

        # 2. Send target OLT identifier (IP or Asset Name)
        logger.info(f"[JumpServer] Enviando busca/seleção do equipamento: '{self.target_olt}'...")
        self.channel.send(f"{self.target_olt}\n")
        time.sleep(1.5)

        resp = self._read_until(["[Host]>", "ID>", "[Account]>", "account", "Select", ">", "#"], timeout=10)
        logger.info(f"[JumpServer] Resposta após enviar '{self.target_olt}':\n{resp[-400:]}")

        # 3. If JumpServer only filtered the asset table and returned to [Host]>, select item '1'
        if "[Host]>" in resp or "Host>" in resp:
            if "no matching" in resp.lower() or "0 item" in resp.lower() or "not found" in resp.lower():
                if self.fallback_target and self.fallback_target != self.target_olt:
                    logger.info(
                        f"[JumpServer] Alvo '{self.target_olt}' não encontrado; tentando nome alternativo '{self.fallback_target}'..."
                    )
                    self.channel.send(f"{self.fallback_target}\n")
                    time.sleep(1.5)
                    resp = self._read_until(["[Host]>", "ID>", "[Account]>", "account", "Select", ">", "#"], timeout=10)
                    logger.info(f"[JumpServer] Resposta após enviar alternativo '{self.fallback_target}':\n{resp[-400:]}")

            if "[Host]>" in resp or "Host>" in resp:
                if "no matching" in resp.lower() or "0 item" in resp.lower() or "not found" in resp.lower():
                    raise ConnectionError(
                        f"Equipamento '{self.target_olt}' não foi encontrado na lista de ativos do seu JumpServer."
                    )
                logger.info("[JumpServer] Tabela filtrada detectada no [Host]>. Selecionando ativo número '1'...")
                self.channel.send("1\n")
                time.sleep(1.5)
                resp = self._read_until(["ID>", "[Account]>", "account", "Select", ">", "#"], timeout=10)
                logger.info(f"[JumpServer] Resposta após selecionar ativo '1':\n{resp[-400:]}")

        # 4. Check if JumpServer asks to choose a System User / Account
        resp_lower = resp.lower()
        if any(k in resp_lower for k in ["id>", "[account]>", "system user", "select account", "enter system user"]):
            selected_id = "1"
            if self.system_user:
                # Look for line like: "1 | auth.minha-olt" or "2 | ..."
                for line in resp.splitlines():
                    if self.system_user.lower() in line.lower():
                        m = re.search(r"^\s*(\d+)\s*\|", line)
                        if m:
                            selected_id = m.group(1)
                            break
            logger.info(
                f"[JumpServer] Seleção de conta solicitada. Enviando opção '{selected_id}' (conta: {self.system_user or 'padrão'})..."
            )
            self.channel.send(f"{selected_id}\n")
            time.sleep(1.5)

        # 5. Wait for Huawei OLT prompt (e.g., MA5608T>, OLT#, <OLT>)
        huawei_prompt = self._wait_for_olt_prompt(timeout=20)
        logger.info(f"[JumpServer] Conectado à OLT! Prompt detectado: '{huawei_prompt}'")

        # 6. Enter enable + config mode and disable paging
        self.enter_config_mode()
        self.set_terminal_paging()

    def _wait_for_olt_prompt(self, timeout: float = 20.0) -> str:
        """Waits until a valid Huawei OLT prompt (ending with > or #) is ready."""
        start_time = time.time()
        buf = ""
        while time.time() - start_time < timeout:
            if self.channel and self.channel.recv_ready():
                chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
                buf += chunk
                clean = strip_ansi(buf)
                lines = [l.strip() for l in clean.splitlines() if l.strip()]
                if lines:
                    last = lines[-1]
                    # Huawei sometimes asks confirm or <cr> on login
                    if "{ <cr>" in last.lower():
                        self.channel.send("\n")
                        time.sleep(0.3)
                        continue
                    if (last.endswith(">") or last.endswith("#")) and "[host]>" not in last.lower():
                        return last
            else:
                # Send a newline if buffer has been quiet to trigger prompt
                if time.time() - start_time > 3.0 and not buf.strip():
                    self.channel.send("\n")
                time.sleep(0.2)

        clean = strip_ansi(buf)
        logger.warning(f"[JumpServer] Timeout aguardando prompt da OLT. Buffer atual:\n{clean[-400:]}")
        return clean.strip()[-30:] if clean else ""

    def _read_available(self) -> str:
        """Reads all currently available output in the buffer."""
        buf = ""
        while self.channel and self.channel.recv_ready():
            chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
            buf += chunk
            time.sleep(0.05)
        return strip_ansi(buf)

    def _read_until(self, matches: list, timeout: Optional[float] = None) -> str:
        """Reads from channel until one of the string matches appears in stripped output."""
        if timeout is None:
            timeout = float(self.timeout)

        start_time = time.time()
        buf = ""

        while time.time() - start_time < timeout:
            if self.channel and self.channel.recv_ready():
                chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
                buf += chunk
                clean_buf = strip_ansi(buf)
                for m in matches:
                    if m in clean_buf:
                        return clean_buf
            time.sleep(0.1)

        return strip_ansi(buf)

    def _send_and_expect_prompt(self, cmd: str, timeout: float = 15.0) -> str:
        """
        Sends a command to the Huawei OLT, automatically handles:
        - '{ <cr>|... }:' interactive parameter prompts (sends Enter)
        - '--More ( Press 'Q' to break )--' pagination (sends Space)
        Returns the full cleaned output once the OLT prompt (> or #) returns.
        """
        if not self.channel:
            raise ConnectionError("Canal SSH fechado.")

        self._read_available()  # Clear any stale data
        logger.debug(f"[OLT CMD] -> {cmd}")
        self.channel.send(f"{cmd}\n")

        start_time = time.time()
        buf = ""
        last_cr_sent = 0.0
        last_space_sent = 0.0

        while time.time() - start_time < timeout:
            if self.channel.recv_ready():
                chunk = self.channel.recv(8192).decode("utf-8", errors="ignore")
                buf += chunk
                clean_buf = strip_ansi(buf)
                lines = [l.strip() for l in clean_buf.splitlines() if l.strip()]
                if not lines:
                    continue

                last_line = lines[-1]
                last_lower = last_line.lower()

                # 1. Handle Huawei '{ <cr>|... }:' prompt
                if "{ <cr>" in last_lower or "<cr>" in last_lower:
                    if time.time() - last_cr_sent > 0.2:
                        logger.debug(f"[OLT] Confirmando prompt <cr> para '{cmd}'...")
                        self.channel.send("\n")
                        last_cr_sent = time.time()
                    continue

                # 2. Handle Huawei '--More ( Press 'Q' to break )--' pagination
                if "--more" in last_lower or "---- more" in last_lower or "press 'q'" in last_lower:
                    if time.time() - last_space_sent > 0.15:
                        self.channel.send(" ")
                        last_space_sent = time.time()
                    continue

                # 3. Check if OLT prompt returned (and we didn't just echo the command line)
                if len(lines) >= 1 and (last_line.endswith("#") or last_line.endswith(">")):
                    # Avoid returning prematurely if the only line is the old prompt before command echo
                    if cmd.strip() == "" or len(lines) > 1 or cmd.strip() not in last_line:
                        # Remove --More-- artifacts from final output
                        cleaned_output = re.sub(
                            r"--+\s*More\s*\([^)]*\)\s*--+", "", clean_buf, flags=re.IGNORECASE
                        )
                        return cleaned_output
            else:
                time.sleep(0.1)

        clean_buf = strip_ansi(buf)
        logger.warning(f"[OLT] Timeout ({timeout}s) no comando '{cmd}'. Saída parcial:\n{clean_buf[-300:]}")
        return clean_buf

    def enter_config_mode(self) -> str:
        """Ensures the OLT is in privileged and config mode (OLT(config)#)."""
        output = ""
        prompt_out = self._send_and_expect_prompt("", timeout=5.0)
        output += prompt_out

        if ">" in prompt_out and "(config" not in prompt_out:
            logger.info("[OLT] Entrando em modo privilegiado (enable)...")
            en_out = self._send_and_expect_prompt("enable", timeout=5.0)
            output += en_out
            prompt_out = en_out

        if "#" in prompt_out and "(config" not in prompt_out:
            logger.info("[OLT] Entrando em modo de configuração (config)...")
            cfg_out = self._send_and_expect_prompt("config", timeout=5.0)
            output += cfg_out

        return output

    def set_terminal_paging(self) -> None:
        """Sets scroll buffer on Huawei OLT to 512 lines to minimize pagination."""
        try:
            logger.info("[OLT] Ajustando paginação do terminal (scroll 512)...")
            self._send_and_expect_prompt("scroll 512", timeout=5.0)
        except Exception as e:
            logger.warning(f"Aviso ao ajustar scroll 512: {e}")

    def exit_interface_mode(self) -> str:
        """Exits any sub-interface back to config mode."""
        output = ""
        prompt_out = self._send_and_expect_prompt("", timeout=5.0)

        tries = 0
        while "(config-if-" in prompt_out and tries < 5:
            prompt_out = self._send_and_expect_prompt("quit", timeout=5.0)
            output += prompt_out
            tries += 1

        self._current_interface = None
        return output

    def send_command(self, command: str, wait_time: float = 1.0) -> str:
        """Sends a display/read command to the OLT and returns the clean output."""
        if not self.channel:
            raise ConnectionError("Não conectado ao JumpServer/OLT.")

        out = self._send_and_expect_prompt(command, timeout=float(self.timeout))
        logger.debug(f"[OLT RESP] ({len(out)} bytes) para '{command}'")
        return out

    def enter_gpon_interface(self, frame: int, slot: int) -> str:
        """Enters interface gpon {frame}/{slot}."""
        self.exit_interface_mode()
        cmd = f"interface gpon {frame}/{slot}"
        logger.info(f"[OLT] Entrando na interface: {cmd}")
        output = self._send_and_expect_prompt(cmd, timeout=10.0)
        self._current_interface = f"gpon {frame}/{slot}"
        return output

    def delete_ont(self, port: int, ont_id: int) -> Tuple[bool, str]:
        """
        Executes 'ont delete {port} {ont_id}' inside the GPON interface.
        Handles '{ <cr> }' and confirmation prompt '(y/n)[n]:'.
        """
        cmd = f"ont delete {port} {ont_id}"
        logger.info(f"[OLT] Executando deleção: {cmd}")
        self._read_available()
        self.channel.send(f"{cmd}\n")

        start_time = time.time()
        buf = ""
        while time.time() - start_time < 15.0:
            if self.channel.recv_ready():
                chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
                buf += chunk
                clean = strip_ansi(buf)
                lines = [l.strip() for l in clean.splitlines() if l.strip()]
                if not lines:
                    continue
                last = lines[-1].lower()
                if "{ <cr>" in last or "<cr>" in last:
                    self.channel.send("\n")
                    time.sleep(0.3)
                    continue
                if "y/n" in last:
                    logger.info("[OLT] Confirmação solicitada pela OLT, enviando 'y'...")
                    self.channel.send("y\n")
                    time.sleep(0.5)
                    continue
                if last.endswith("#") or last.endswith(">"):
                    if len(lines) > 1:
                        break
            time.sleep(0.1)

        output = strip_ansi(buf)
        output_lower = output.lower()
        if "failure" in output_lower or "error" in output_lower:
            logger.error(f"Erro ao deletar ONT {port}/{ont_id}: {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def undo_service_port(self, service_port_id: int) -> Tuple[bool, str]:
        """Executes 'undo service-port {service_port_id}' in config mode."""
        self.exit_interface_mode()
        cmd = f"undo service-port {service_port_id}"
        logger.info(f"[OLT] Removendo service-port: {cmd}")
        self._read_available()
        self.channel.send(f"{cmd}\n")

        start_time = time.time()
        buf = ""
        while time.time() - start_time < 15.0:
            if self.channel.recv_ready():
                chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
                buf += chunk
                clean = strip_ansi(buf)
                lines = [l.strip() for l in clean.splitlines() if l.strip()]
                if not lines:
                    continue
                last = lines[-1].lower()
                if "{ <cr>" in last or "<cr>" in last:
                    self.channel.send("\n")
                    time.sleep(0.3)
                    continue
                if "y/n" in last:
                    self.channel.send("y\n")
                    time.sleep(0.5)
                    continue
                if last.endswith("#") or last.endswith(">"):
                    if len(lines) > 1:
                        break
            time.sleep(0.1)

        output = strip_ansi(buf)
        output_lower = output.lower()
        if "failure" in output_lower or "error" in output_lower:
            logger.error(f"Erro ao remover service-port {service_port_id}: {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def execute_config_command(self, cmd: str) -> Tuple[bool, str]:
        """
        Executes a configuration/rollback command on the OLT via JumpServer channel,
        automatically handling Huawei '{ <cr> }' and '(y/n)' prompts.
        """
        if not self.channel:
            raise ConnectionError("Não conectado ao JumpServer/OLT.")

        logger.info(f"[OLT] Executando comando de configuração: {cmd}")
        self._read_available()
        self.channel.send(f"{cmd}\n")

        start_time = time.time()
        buf = ""
        last_cr_sent = 0.0
        while time.time() - start_time < 15.0:
            if self.channel.recv_ready():
                chunk = self.channel.recv(4096).decode("utf-8", errors="ignore")
                buf += chunk
                clean = strip_ansi(buf)
                lines = [l.strip() for l in clean.splitlines() if l.strip()]
                if not lines:
                    continue
                last = lines[-1].lower()
                if "{ <cr>" in last or "<cr>" in last:
                    if time.time() - last_cr_sent > 0.25:
                        self.channel.send("\n")
                        last_cr_sent = time.time()
                    continue
                if "y/n" in last:
                    self.channel.send("y\n")
                    time.sleep(0.4)
                    continue
                if last.endswith("#") or last.endswith(">"):
                    if len(lines) > 1 or cmd.strip() not in last:
                        break
            else:
                time.sleep(0.1)

        output = strip_ansi(buf)
        output_lower = output.lower()
        if "failure:" in output_lower or "error:" in output_lower or "parameter error" in output_lower:
            logger.error(f"[OLT] Erro no comando '{cmd}': {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def disconnect(self) -> None:
        """Closes the channel and JumpServer SSH connection."""
        try:
            if self.channel:
                self.channel.send("quit\n")
                time.sleep(0.2)
                self.channel.close()
        except Exception:
            pass

        try:
            if self.ssh:
                self.ssh.close()
        except Exception:
            pass

        self.channel = None
        self.ssh = None
        logger.info("[JumpServer] Sessão encerrada.")
