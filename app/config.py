import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

# Load .env if present
load_dotenv()


@dataclass
class OltSettings:
    host: str = field(default_factory=lambda: os.getenv("OLT_HOST", ""))
    port: int = field(default_factory=lambda: int(os.getenv("OLT_PORT", "22")))
    username: str = field(default_factory=lambda: os.getenv("OLT_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("OLT_PASSWORD", ""))
    enable_password: Optional[str] = field(
        default_factory=lambda: os.getenv("OLT_ENABLE_PASSWORD") or None
    )
    timeout: int = field(default_factory=lambda: int(os.getenv("OLT_TIMEOUT", "30")))
    device_type: str = field(
        default_factory=lambda: os.getenv("OLT_DEVICE_TYPE", "huawei_smartax")
    )


@dataclass
class JumpServerSettings:
    enabled: bool = field(
        default_factory=lambda: os.getenv("JUMPSERVER_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    host: str = field(default_factory=lambda: os.getenv("JUMPSERVER_HOST", ""))
    port: int = field(default_factory=lambda: int(os.getenv("JUMPSERVER_PORT", "2222")))
    username: str = field(default_factory=lambda: os.getenv("JUMPSERVER_USER", ""))
    password: str = field(default_factory=lambda: os.getenv("JUMPSERVER_PASSWORD", ""))
    system_account: str = field(
        default_factory=lambda: os.getenv("JUMPSERVER_ACCOUNT", "")
    )


@dataclass
class CleanupSettings:
    time_value: int = field(
        default_factory=lambda: int(float(os.getenv("CLEANUP_TIME_VALUE", "30")))
    )
    time_unit: str = field(
        default_factory=lambda: os.getenv("CLEANUP_TIME_UNIT", "dias").lower()
    )  # 'dias', 'semanas', 'meses'
    days_offline_threshold: Optional[int] = field(default=None)

    dry_run: bool = field(
        default_factory=lambda: os.getenv("CLEANUP_DRY_RUN", "true").lower() in ("true", "1", "yes")
    )
    delete_never_connected: bool = field(
        default_factory=lambda: os.getenv("CLEANUP_DELETE_NEVER_CONNECTED", "false").lower()
        in ("true", "1", "yes")
    )
    description_whitelist: List[str] = field(
        default_factory=lambda: [
            w.strip().upper()
            for w in os.getenv("CLEANUP_WHITELIST", "VIP,DEDICADO,GOVERNO,EMPRESA,TESTE").split(",")
            if w.strip()
        ]
    )
    backup_dir: str = field(
        default_factory=lambda: os.getenv("CLEANUP_BACKUP_DIR", "./backups")
    )
    log_dir: str = field(default_factory=lambda: os.getenv("CLEANUP_LOG_DIR", "./logs"))

    def __post_init__(self):
        if self.days_offline_threshold is None:
            self.days_offline_threshold = self.calculate_days(self.time_value, self.time_unit)
        else:
            self.days_offline_threshold = int(float(self.days_offline_threshold))

    @staticmethod
    def calculate_days(value: int, unit: str) -> int:
        """Converts dias, semanas, or meses into total integer days."""
        val_int = int(float(value))
        unit_clean = unit.strip().lower()
        if "semana" in unit_clean:
            return val_int * 7
        elif "mês" in unit_clean or "mes" in unit_clean:
            return val_int * 30
        return val_int

    def set_time_threshold(self, value: int, unit: str):
        self.time_value = int(float(value))
        self.time_unit = unit.lower()
        self.days_offline_threshold = self.calculate_days(self.time_value, unit)


def update_env_file(key_values: dict, env_path: str = ".env") -> bool:
    """
    Updates or appends key-value pairs in the .env file.
    Preserves comments and existing unmentioned keys.
    Reloads dotenv immediately.
    """
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updated_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in key_values:
                new_val = str(key_values[key])
                new_lines.append(f"{key}={new_val}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any new keys that were not previously in .env
    for key, val in key_values.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Reload environment into current process
    load_dotenv(env_path, override=True)
    return True
