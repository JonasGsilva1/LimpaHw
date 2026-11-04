import time
import logging
from typing import Tuple, Optional
from app.config import OltSettings

logger = logging.getLogger("limpahw.mock")


class MockOltClient:
    """
    Mock Huawei OLT Client for demonstrations, dry-runs, and offline tests.
    Simulates a Huawei MA5800/MA5608T OLT.
    """

    def __init__(self, settings: Optional[OltSettings] = None):
        self.settings = settings
        self.connected = False
        self.current_interface = None
        self.deleted_service_ports = set()
        self.deleted_onts = set()

    def connect(self) -> None:
        logger.info("[MOCK] Conectando a OLT simulada...")
        time.sleep(0.2)
        self.connected = True
        logger.info("[MOCK] Conexão simulada estabelecida!")

    def disconnect(self) -> None:
        self.connected = False
        logger.info("[MOCK] Desconectado da OLT simulada.")

    def enter_config_mode(self) -> str:
        return ""

    def exit_interface_mode(self) -> str:
        self.current_interface = None
        return ""

    def enter_gpon_interface(self, frame: int, slot: int) -> str:
        self.current_interface = f"gpon {frame}/{slot}"
        return f"Enter gpon interface {frame}/{slot}"

    def send_command(self, command: str, wait_time: float = 1.0) -> str:
        cmd = command.strip()

        # display board 0
        if cmd.startswith("display board"):
            return """
  -------------------------------------------------------------------------
  SlotID  BoardName  Status         SubType0 SubType1    Online/Offline
  -------------------------------------------------------------------------
  0       H901PISA   Normal         
  1       H901GPHF   Normal         
  8       H901MPLA   Active_normal  
  -------------------------------------------------------------------------
"""

        # display ont info summary 0/1/0
        if "display ont info summary" in cmd:
            return """
  ------------------------------------------------------------------------------
  In port 0/1/0, the total of ONTs is: 5, online: 1, offline: 4
  ------------------------------------------------------------------------------
  ONT  Run     Config  Match  Protect  ONT
  ID   State   State   State  State    Distance(m)
  ------------------------------------------------------------------------------
  0    online  normal  match  none     320
  1    offline normal  match  none     -
  2    offline normal  match  none     -
  3    offline normal  match  none     -
  4    offline initial match  none     -
  ------------------------------------------------------------------------------
"""

        # display ont info 0 1 0 1
        if "display ont info 0 1 0 1" in cmd:
            return """
  -----------------------------------------------------------------------------
  ONT-ID                       : 1
  MAC                          : 00e0-fc11-2233
  SN                           : 4857544311223344 (HWTC-11223344)
  Control flag                 : active
  Run state                    : offline
  Config state                 : normal
  Description                  : maria.santos_fibra
  Last down cause              : dying-gasp
  Last up time                 : 2024-01-10 10:00:00
  Last down time               : 2024-02-01 14:30:00
  ONT offline duration         : 65 day(s), 4 hour(s), 10 minute(s), 0 second(s)
  -----------------------------------------------------------------------------
"""

        # display ont info 0 1 0 2
        if "display ont info 0 1 0 2" in cmd:
            return """
  -----------------------------------------------------------------------------
  ONT-ID                       : 2
  MAC                          : 00e0-fc44-5566
  SN                           : 4857544322334455 (HWTC-22334455)
  Control flag                 : active
  Run state                    : offline
  Config state                 : normal
  Description                  : joao.pereira_100m
  Last down cause              : loss of signal
  Last up time                 : 2024-02-01 08:00:00
  Last down time               : 2024-02-25 09:15:00
  ONT offline duration         : 42 day(s), 1 hour(s), 0 minute(s), 0 second(s)
  -----------------------------------------------------------------------------
"""

        # display ont info 0 1 0 3 (VIP)
        if "display ont info 0 1 0 3" in cmd:
            return """
  -----------------------------------------------------------------------------
  ONT-ID                       : 3
  MAC                          : 00e0-fc77-8899
  SN                           : 4857544355667788 (HWTC-55667788)
  Control flag                 : active
  Run state                    : offline
  Config state                 : normal
  Description                  : VIP_HOSPITAL_CENTRAL
  Last down cause              : loss of signal
  Last up time                 : 2024-01-15 09:00:00
  Last down time               : 2024-02-10 11:15:00
  ONT offline duration         : 55 day(s), 2 hour(s), 0 minute(s), 0 second(s)
  -----------------------------------------------------------------------------
"""

        # display ont info 0 1 0 4 (Nunca conectou)
        if "display ont info 0 1 0 4" in cmd:
            return """
  -----------------------------------------------------------------------------
  ONT-ID                       : 4
  MAC                          : -
  SN                           : 4857544399001122 (HWTC-99001122)
  Control flag                 : active
  Run state                    : offline
  Config state                 : initial
  Description                  : instalacao_pendente_ana
  Last down cause              : -
  Last up time                 : -
  Last down time               : -
  ONT offline duration         : -
  -----------------------------------------------------------------------------
"""

        # display current-configuration ont 0/1/0 <ont_id>
        if "display current-configuration ont" in cmd:
            if " 1" in cmd:
                return """
[ont-configuration]
<ont-configuration>
ont add 0 1 sn-auth "4857544311223344" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "maria.santos_fibra"
ont port native-vlan 0 1 eth 1 vlan 100 priority 0
#
[service-port]
<service-port>
service-port 1050 vlan 100 gpon 0/1/0 ont 1 gemport 1 multi-service user-vlan 100 tag-transform translate
service-port 1051 vlan 200 gpon 0/1/0 ont 1 gemport 2 multi-service user-vlan 200 tag-transform translate
#
return
"""
            if " 2" in cmd:
                return """
[ont-configuration]
<ont-configuration>
ont add 0 2 sn-auth "4857544322334455" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "joao.pereira_100m"
ont port native-vlan 0 2 eth 1 vlan 100 priority 0
#
[service-port]
<service-port>
service-port 1080 vlan 100 gpon 0/1/0 ont 2 gemport 1 multi-service user-vlan 100 tag-transform translate
#
return
"""
            if " 3" in cmd:
                return """
[ont-configuration]
<ont-configuration>
ont add 0 3 sn-auth "4857544355667788" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "VIP_HOSPITAL_CENTRAL"
#
[service-port]
<service-port>
service-port 2050 vlan 500 gpon 0/1/0 ont 3 gemport 1 multi-service user-vlan 500 tag-transform translate
#
return
"""

        # display service-port port 0/1/0 ont <ont_id>
        if "display service-port port" in cmd:
            if "ont 1" in cmd:
                return """
  INDEX VLAN VLAN  PORT F/ S/ P  ONT GEM  FLOW  FLOW       RX TX  STATE
        ID   ATTR  TYPE          ID  PORT TYPE  PARA
  ---------------------------------------------------------------------
  1050  100  common gpon 0/1 /0  1   1    vlan  100        -  -   down
  1051  200  common gpon 0/1 /0  1   2    vlan  200        -  -   down
  ---------------------------------------------------------------------
"""
            if "ont 2" in cmd:
                return """
  INDEX VLAN VLAN  PORT F/ S/ P  ONT GEM  FLOW  FLOW       RX TX  STATE
        ID   ATTR  TYPE          ID  PORT TYPE  PARA
  ---------------------------------------------------------------------
  1080  100  common gpon 0/1 /0  2   1    vlan  100        -  -   down
  ---------------------------------------------------------------------
"""
            return ""

        return ""

    def undo_service_port(self, sp_id: int) -> Tuple[bool, str]:
        self.deleted_service_ports.add(sp_id)
        logger.info(f"[MOCK] undo service-port {sp_id} executado com sucesso.")
        return True, "Success"

    def delete_ont(self, port: int, ont_id: int) -> Tuple[bool, str]:
        self.deleted_onts.add((port, ont_id))
        logger.info(f"[MOCK] ont delete {port} {ont_id} executado com sucesso.")
        return True, "Success"

    def execute_config_command(self, cmd: str) -> Tuple[bool, str]:
        logger.info(f"[MOCK] comando executado com sucesso: {cmd}")
        return True, f"[MOCK] {cmd} -> OK"
