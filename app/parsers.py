import re
from datetime import datetime
from typing import List, Optional, Dict
from app.models import OntRecord, ServicePortRecord


def parse_board_slots(output: str) -> List[int]:
    """
    Parses `display board 0` to find slots with active GPON/EPON/XGPON/Combo-PON boards.
    Looks for boards like GPBD, GPFD, GPHF, GPLF, CGHF, XGHD, etc.
    """
    gpon_slots: List[int] = []
    control_keywords = ("SCU", "MPL", "PIS", "PRT", "GIC", "X2C", "CIL", "CIU", "EMU", "MPW", "PWR", "FAN")
    pon_keywords = ("GP", "XG", "CG", "PON", "EP", "TW")

    lines = output.splitlines()
    for line in lines:
        match = re.search(r"^\s*(\d+)\s+([A-Za-z0-9_-]+)\s+([A-Za-z0-9_-]+)", line)
        if match:
            slot_id = int(match.group(1))
            board_name = match.group(2).upper()
            status = match.group(3).lower()

            if any(ctrl in board_name for ctrl in control_keywords):
                continue

            if "normal" in status or "active" in status:
                if any(x in board_name for x in pon_keywords):
                    gpon_slots.append(slot_id)
    return gpon_slots


def parse_ont_summary(
    output: str,
    frame: int = 0,
    slot: int = 0,
    port: int = 0,
    reference_time: Optional[datetime] = None,
) -> List[OntRecord]:
    """
    Parses `display ont info summary 0/{slot}/{port}`.
    Supports both:
    1) Standard Huawei two-table summary output:
       - Table 1: ONT ID | Run State | Last UpTime | Last DownTime | Last DownCause
       - Table 2: ONT ID | SN | Type | Distance | Rx/Tx power | Description (Customer Login)
    2) Compact summary output:
       - ONT ID | Run State | Config State | Match State
    """
    if reference_time is None:
        reference_time = datetime.now()

    records_by_id: Dict[int, OntRecord] = {}
    lines = output.splitlines()

    # Regex for Table 1 with timestamps:
    # e.g. "1  offline  2026-08-01 10:20:00  2026-08-15 14:30:22  dying-gasp"
    # or   "2  offline  -                    -                    -"
    ts_pattern = r"(?:\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?|\d{2}[-/]\d{2}[-/]\d{4}\s+\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?|-)"
    table1_ts_re = re.compile(
        rf"^\s*(\d+)\s+(online|offline)\s+({ts_pattern})\s+({ts_pattern})\s*(.*)$",
        re.IGNORECASE,
    )

    # Regex for Table 2 (SN, Type, Distance, Rx/Tx power, Description):
    # e.g. "1  48575443AABBCCDD  EG8145V5  -  -/-  joao.silva_fibra"
    table2_sn_re = re.compile(
        r"^\s*(\d+)\s+([0-9A-Fa-f]{12,20}|[A-Za-z]{4}[0-9A-Fa-f]{8,12})\s+(\S+)\s+(\d+|-)\s+(\S+/\S+)\s*(.*)$"
    )

    # Regex for Compact Table 1 (ONT ID | Run State | Config State | Match State):
    compact_re = re.compile(
        r"^\s*(\d+)\s+(online|offline)\s+([a-zA-Z_-]+)?\s*([a-zA-Z_-]+)?",
        re.IGNORECASE,
    )

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("---") or line_stripped.startswith("==="):
            continue
        if line_stripped.lower().startswith("in port") or line_stripped.lower().startswith("total"):
            continue

        # 1. Try Table 2 (SN + Description) first
        m2 = table2_sn_re.match(line)
        if m2:
            ont_id = int(m2.group(1))
            sn = m2.group(2).strip()
            desc = m2.group(6).strip()
            if desc == "-" or desc.upper() == "ONT_NO_DESCRIPTION":
                desc = ""

            if ont_id in records_by_id:
                ont = records_by_id[ont_id]
                ont.sn = sn
                if desc:
                    ont.description = desc
                    ont.customer_login = desc
            else:
                records_by_id[ont_id] = OntRecord(
                    frame=frame,
                    slot=slot,
                    port=port,
                    ont_id=ont_id,
                    run_state="offline",
                    sn=sn,
                    description=desc,
                    customer_login=desc,
                )
            continue

        # 2. Try Table 1 with Last UpTime / Last DownTime / Last DownCause
        m1_ts = table1_ts_re.match(line)
        if m1_ts:
            ont_id = int(m1_ts.group(1))
            run_state = m1_ts.group(2).lower()
            up_str = m1_ts.group(3).strip()
            down_str = m1_ts.group(4).strip()
            cause_str = m1_ts.group(5).strip()

            up_dt = _parse_olt_datetime(up_str) if up_str != "-" else None
            down_dt = _parse_olt_datetime(down_str) if down_str != "-" else None
            never_conn = up_dt is None and down_dt is None

            ont = records_by_id.get(ont_id)
            if not ont:
                ont = OntRecord(
                    frame=frame,
                    slot=slot,
                    port=port,
                    ont_id=ont_id,
                    run_state=run_state,
                )
                records_by_id[ont_id] = ont

            ont.run_state = run_state
            ont.last_up_time = up_dt
            ont.last_down_time = down_dt
            ont.last_down_cause = "" if cause_str == "-" else cause_str
            ont.never_connected = never_conn
            ont.calculated_offline_days = calculate_offline_days(ont, reference_time)
            continue

        # 3. Try Compact Table 1 (ONT ID | Run State | Config State | Match State)
        m_compact = compact_re.match(line)
        if m_compact:
            ont_id = int(m_compact.group(1))
            run_state = m_compact.group(2).lower()
            config_state = (m_compact.group(3) or "").lower()
            match_state = (m_compact.group(4) or "").lower()

            if ont_id not in records_by_id:
                records_by_id[ont_id] = OntRecord(
                    frame=frame,
                    slot=slot,
                    port=port,
                    ont_id=ont_id,
                    run_state=run_state,
                    config_state=config_state,
                    match_state=match_state,
                )

    return [records_by_id[k] for k in sorted(records_by_id.keys())]


def parse_ont_detail(output: str, ont: OntRecord, reference_time: Optional[datetime] = None) -> OntRecord:
    """
    Parses detailed info from `display ont info 0 {slot} {port} {ont_id}`
    Enriches the OntRecord with SN, MAC, Description, Last Down Time, Offline Duration, etc.
    """
    if reference_time is None:
        reference_time = datetime.now()

    ont.raw_info = output

    # MAC: e.g. "MAC : 00e0-fc12-3456" or "-"
    mac_match = re.search(r"MAC\s*:\s*([0-9a-fA-F\-:.]+)", output)
    if mac_match:
        mac_val = mac_match.group(1).strip()
        if mac_val != "-":
            ont.mac = mac_val

    # SN: e.g. "SN : 4857544312345678 (HWTC-12345678)" or "SN : 4857544312345678"
    sn_match = re.search(r"SN\s*:\s*([0-9a-zA-Z]+)(?:\s*\(([^)]+)\))?", output)
    if sn_match:
        full_sn = sn_match.group(1)
        alias = sn_match.group(2)
        ont.sn = f"{full_sn} ({alias})" if alias else full_sn

    # Description: e.g. "Description : RUA_DAS_FLORES_123"
    desc_match = re.search(r"Description\s*:\s*(.*)", output)
    if desc_match:
        desc_val = desc_match.group(1).strip()
        if desc_val and desc_val != "-" and desc_val.upper() != "ONT_NO_DESCRIPTION":
            ont.description = desc_val
            ont.customer_login = desc_val

    # Run state
    state_match = re.search(r"Run state\s*:\s*([a-zA-Z0-9_-]+)", output)
    if state_match:
        ont.run_state = state_match.group(1).lower()

    # Config state
    cfg_match = re.search(r"Config state\s*:\s*([a-zA-Z0-9_-]+)", output)
    if cfg_match:
        ont.config_state = cfg_match.group(1).lower()

    # Last down cause
    cause_match = re.search(r"Last down cause\s*:\s*(.*)", output)
    if cause_match:
        cause_val = cause_match.group(1).strip()
        if cause_val != "-":
            ont.last_down_cause = cause_val

    # Last up time: e.g. "2024-05-10 10:20:30" or "2024-05-10 10:20:30-03:00" or "-"
    up_match = re.search(r"Last up time\s*:\s*([\d\-/]+\s+[\d:]+(?:[+-]\d{2}:\d{2})?|-)", output)
    if up_match:
        up_str = up_match.group(1).strip()
        if up_str != "-":
            ont.last_up_time = _parse_olt_datetime(up_str)

    # Last down time: e.g. "2024-06-01 15:40:12" or "2024-06-01 15:40:12-03:00" or "-"
    down_match = re.search(r"Last down time\s*:\s*([\d\-/]+\s+[\d:]+(?:[+-]\d{2}:\d{2})?|-)", output)
    if down_match:
        down_str = down_match.group(1).strip()
        if down_str != "-":
            ont.last_down_time = _parse_olt_datetime(down_str)

    # ONT offline duration: e.g. "45 day(s), 3 hour(s), 10 minute(s), 12 second(s)" or "-"
    dur_match = re.search(r"ONT offline duration\s*:\s*(.*)", output)
    if dur_match:
        dur_str = dur_match.group(1).strip()
        if dur_str != "-":
            ont.offline_duration_str = dur_str

    # Determine if never connected
    if (ont.last_down_time is None and ont.last_up_time is None) and (
        ont.offline_duration_str == "" or ont.offline_duration_str == "-"
    ):
        ont.never_connected = True
    else:
        ont.never_connected = False

    # Calculate offline days
    ont.calculated_offline_days = calculate_offline_days(ont, reference_time)
    return ont


def _parse_olt_datetime(dt_str: str) -> Optional[datetime]:
    """
    Helper to parse Huawei OLT datetime formats like:
    - '2024-05-10 10:20:30'
    - '2024-05-10 10:20:30-03:00'
    - '2024/05/10 10:20:30'
    - '10/05/2024 10:20:30'
    """
    cleaned = dt_str.strip()
    if not cleaned or cleaned == "-":
        return None

    # Strip trailing timezone offset like -03:00 or +08:00 if present
    cleaned = re.sub(r"[+-]\d{2}:\d{2}$", "", cleaned).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def calculate_offline_days(ont: OntRecord, reference_time: datetime) -> Optional[float]:
    """
    Calculates offline duration in days based on:
    1. 'ONT offline duration' string if available (e.g. '45 day(s), 3 hour(s)')
    2. 'Last down time' difference against reference_time
    """
    if ont.offline_duration_str and ont.offline_duration_str != "-":
        days = 0
        hours = 0
        minutes = 0

        d_match = re.search(r"(\d+)\s*day", ont.offline_duration_str, re.IGNORECASE)
        h_match = re.search(r"(\d+)\s*hour", ont.offline_duration_str, re.IGNORECASE)
        m_match = re.search(r"(\d+)\s*minute", ont.offline_duration_str, re.IGNORECASE)

        if d_match:
            days = int(d_match.group(1))
        if h_match:
            hours = int(h_match.group(1))
        if m_match:
            minutes = int(m_match.group(1))

        if d_match or h_match or m_match:
            return round(days + (hours / 24.0) + (minutes / 1440.0), 2)

    if ont.last_down_time:
        delta = reference_time - ont.last_down_time
        total_seconds = delta.total_seconds()
        if total_seconds < 0:
            return 0.0
        return round(total_seconds / 86400.0, 2)

    return None


def parse_service_ports_from_config(config_text: str) -> List[int]:
    """
    Parses `display current-configuration ont 0/{slot}/{port} {ont_id}` output
    and extracts all service-port index numbers.
    """
    service_ports: List[int] = []
    matches = re.finditer(r"^\s*service-port\s+(\d+)\b", config_text, re.MULTILINE | re.IGNORECASE)
    for m in matches:
        sp_id = int(m.group(1))
        if sp_id not in service_ports:
            service_ports.append(sp_id)
    return service_ports


def parse_service_ports_from_table(table_text: str) -> List[ServicePortRecord]:
    """
    Parses `display service-port port 0/{slot}/{port} ont {ont_id}` output.
    """
    results: List[ServicePortRecord] = []
    lines = table_text.splitlines()
    table_started = False

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        if "INDEX" in line and ("VLAN" in line or "PORT" in line):
            table_started = True
            continue

        if table_started:
            if line_stripped.startswith("---") or line_stripped.startswith("==="):
                continue
            if line_stripped.startswith("Total"):
                continue

            match = re.search(r"^\s*(\d+)\s+(\d+)?\s*([a-zA-Z_-]+)?\s*([a-zA-Z_-]+)?", line)
            if match:
                index = int(match.group(1))
                vlan = int(match.group(2)) if match.group(2) else None
                vlan_attr = match.group(3) if match.group(3) else None
                port_type = match.group(4) if match.group(4) else "gpon"

                record = ServicePortRecord(
                    index=index,
                    vlan=vlan,
                    vlan_attr=vlan_attr,
                    port_type=port_type,
                    raw_line=line_stripped,
                )
                results.append(record)
    return results
