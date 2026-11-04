import time
import logging
import re
from typing import Optional, List, Tuple
from netmiko import ConnectHandler
from app.config import OltSettings

logger = logging.getLogger("limpahw.olt")


class HuaweiOltClient:
    """
    SSH Client for Huawei SmartAX OLTs (MA5600, MA5608T, MA5800, etc.).
    Uses Netmiko under the hood with huawei_smartax driver.
    """

    def __init__(self, settings: OltSettings):
        self.settings = settings
        self.connection = None
        self._in_config_mode = False
        self._current_interface: Optional[str] = None

    def connect(self) -> None:
        """Establishes SSH connection to the Huawei OLT."""
        device_params = {
            "device_type": self.settings.device_type,
            "host": self.settings.host,
            "port": self.settings.port,
            "username": self.settings.username,
            "password": self.settings.password,
            "timeout": self.settings.timeout,
            "session_log": None,  # Can be directed to a log file if needed
            "fast_cli": False,
        }

        if self.settings.enable_password:
            device_params["secret"] = self.settings.enable_password

        logger.info(f"Conectando a OLT {self.settings.host}:{self.settings.port} via SSH...")
        self.connection = ConnectHandler(**device_params)

        # Enter enable mode if needed
        try:
            self.connection.enable()
        except Exception as e:
            logger.debug(f"Enable mode check: {e}")

        # Enter config mode and adjust scroll/screen settings
        self.enter_config_mode()
        self.set_terminal_paging()
        logger.info(f"Conexão estabelecida com sucesso com {self.settings.host}.")

    def set_terminal_paging(self) -> None:
        """Configures scroll to prevent paging pauses (--More--)."""
        try:
            # Huawei SmartAX command to set scroll buffer
            self.connection.send_command_timing("scroll 512")
        except Exception as e:
            logger.warning(f"Aviso ao ajustar scroll 512: {e}")

    def enter_config_mode(self) -> str:
        """Ensures the OLT is in config mode (OLT(config)#)."""
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        # Check prompt
        prompt = self.connection.find_prompt()
        if "(config" not in prompt:
            output = self.connection.send_command_timing("config")
            self._in_config_mode = True
            return output
        self._in_config_mode = True
        return ""

    def exit_interface_mode(self) -> str:
        """Exits any sub-interface mode back to config mode."""
        if not self.connection:
            return ""
        prompt = self.connection.find_prompt()
        output = ""
        while "(config-if-" in prompt:
            output += self.connection.send_command_timing("quit")
            prompt = self.connection.find_prompt()
        self._current_interface = None
        return output

    def send_command(self, command: str, wait_time: float = 1.0) -> str:
        """Sends a read/display command and returns the text response."""
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        logger.debug(f"Enviando comando: {command}")
        output = self.connection.send_command(
            command,
            read_timeout=float(self.settings.timeout),
        )
        return output

    def enter_gpon_interface(self, frame: int, slot: int) -> str:
        """
        Enters interface gpon {frame}/{slot}.
        Example: interface gpon 0/1
        """
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        self.exit_interface_mode()
        cmd = f"interface gpon {frame}/{slot}"
        logger.info(f"Entrando na interface: {cmd}")
        output = self.connection.send_command_timing(cmd)
        self._current_interface = f"gpon {frame}/{slot}"
        return output

    def delete_ont(self, port: int, ont_id: int) -> Tuple[bool, str]:
        """
        Executes 'ont delete {port} {ont_id}' inside the current gpon interface.
        Handles possible confirmation prompt '(y/n)[n]:'.
        """
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        cmd = f"ont delete {port} {ont_id}"
        logger.info(f"Executando delecao: {cmd}")
        output = self.connection.send_command_timing(cmd)

        # Check for confirmation prompt
        if "y/n" in output.lower():
            logger.info("Confirmacao solicitada pela OLT, enviando 'y'...")
            output += self.connection.send_command_timing("y")

        # Check for failure indications
        output_lower = output.lower()
        if "failure" in output_lower or "error" in output_lower or "unknown command" in output_lower:
            logger.error(f"Erro ao deletar ONT {port}/{ont_id}: {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def undo_service_port(self, service_port_id: int) -> Tuple[bool, str]:
        """
        Executes 'undo service-port {service_port_id}' in config mode.
        """
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        self.exit_interface_mode()
        cmd = f"undo service-port {service_port_id}"
        logger.info(f"Removendo service-port: {cmd}")
        output = self.connection.send_command_timing(cmd)

        # In case it asks for confirmation
        if "y/n" in output.lower():
            output += self.connection.send_command_timing("y")

        output_lower = output.lower()
        if "failure" in output_lower or "error" in output_lower:
            logger.error(f"Erro ao remover service-port {service_port_id}: {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def execute_config_command(self, cmd: str) -> Tuple[bool, str]:
        """
        Executes a configuration/rollback command on the OLT, automatically handling
        Huawei '{ <cr>|... }:' interactive prompts and '(y/n)' confirmations.
        """
        if not self.connection:
            raise ConnectionError("OLT não conectada.")

        logger.info(f"Executando comando de configuração: {cmd}")
        output = self.connection.send_command_timing(cmd)

        if "{ <cr>" in output.lower() or "<cr>" in output.lower():
            output += self.connection.send_command_timing("\n")

        if "y/n" in output.lower():
            output += self.connection.send_command_timing("y")

        output_lower = output.lower()
        if "failure:" in output_lower or "error:" in output_lower or "parameter error" in output_lower:
            logger.error(f"Erro no comando '{cmd}': {output.strip()}")
            return False, output.strip()

        return True, output.strip()

    def disconnect(self) -> None:
        """Closes the SSH connection."""
        if self.connection:
            try:
                self.exit_interface_mode()
                self.connection.disconnect()
            except Exception as e:
                logger.debug(f"Erro ao desconectar: {e}")
            finally:
                self.connection = None
                logger.info("Desconectado da OLT.")
