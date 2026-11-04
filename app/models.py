from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class OltTarget:
    host: str
    port: int = 22
    username: str = "admin"
    password: str = ""
    enable_password: Optional[str] = None
    name: str = ""
    slot: Optional[int] = None
    port_gpon: Optional[int] = None

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.host})" if self.name else self.host


@dataclass
class ServicePortRecord:
    index: int
    vlan: Optional[int] = None
    port_type: str = "gpon"
    frame: int = 0
    slot: int = 0
    port: int = 0
    ont_id: int = 0
    gem_port: Optional[int] = None
    vlan_attr: Optional[str] = None
    user_vlan: Optional[int] = None
    raw_line: str = ""


@dataclass
class OntRecord:
    frame: int
    slot: int
    port: int
    ont_id: int
    run_state: str  # online, offline
    config_state: str = ""
    match_state: str = ""
    sn: str = ""
    mac: str = ""
    description: str = ""
    customer_login: str = ""  # Derived from description (usually customer login in ERP)
    last_down_cause: str = ""
    last_down_time: Optional[datetime] = None
    last_up_time: Optional[datetime] = None
    offline_duration_str: str = ""
    calculated_offline_days: Optional[float] = None
    never_connected: bool = False
    raw_info: str = ""
    olt_host: str = ""
    olt_name: str = ""

    def __post_init__(self):
        if not self.customer_login and self.description:
            self.customer_login = self.description.strip()

    @property
    def display_login(self) -> str:
        return self.customer_login or self.description or "-"

    @property
    def location(self) -> str:
        return f"{self.frame}/{self.slot}/{self.port}:{self.ont_id}"

    @property
    def gpon_port(self) -> str:
        return f"{self.frame}/{self.slot}/{self.port}"


@dataclass
class CleanupCandidate:
    ont: OntRecord
    service_ports: List[int] = field(default_factory=list)
    raw_current_config: str = ""
    eligible_for_deletion: bool = False
    skip_reason: str = ""
    backup_file_path: Optional[str] = None
    rollback_file_path: Optional[str] = None
    selected_by_user: bool = True  # User can check/uncheck this ONT


@dataclass
class CleanupResult:
    ont: OntRecord
    service_ports_deleted: List[int] = field(default_factory=list)
    service_ports_failed: List[int] = field(default_factory=list)
    ont_deleted: bool = False
    success: bool = False
    error_message: str = ""
    backup_file: Optional[str] = None
    rollback_file: Optional[str] = None
    rollback_commands: List[str] = field(default_factory=list)
