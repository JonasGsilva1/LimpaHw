import pytest
from datetime import datetime
from app.models import OntRecord
from app.parsers import (
    parse_board_slots,
    parse_ont_summary,
    parse_ont_detail,
    calculate_offline_days,
    parse_service_ports_from_config,
    parse_service_ports_from_table,
)

SAMPLE_BOARD_OUTPUT = """
  -------------------------------------------------------------------------
  SlotID  BoardName  Status         SubType0 SubType1    Online/Offline
  -------------------------------------------------------------------------
  0       H901PISA   Normal         
  1       H901GPHF   Normal         
  2       H901GPHF   Normal         
  7       H901CIUA   Normal         
  8       H901MPLA   Active_normal  
  9       H901MPLA   Standby_normal 
  -------------------------------------------------------------------------
"""

SAMPLE_ONT_SUMMARY = """
  ------------------------------------------------------------------------------
  In port 0/1/0, the total of ONTs is: 4, online: 2, offline: 2
  ------------------------------------------------------------------------------
  ONT  Run     Config  Match  Protect  ONT
  ID   State   State   State  State    Distance(m)
  ------------------------------------------------------------------------------
  0    online  normal  match  none     350
  1    offline normal  match  none     -
  2    online  normal  match  none     1200
  3    offline initial match  none     -
  ------------------------------------------------------------------------------
"""

SAMPLE_ONT_DETAIL_WITH_DURATION = """
  -----------------------------------------------------------------------------
  ONT-ID                       : 1
  MAC                          : -
  SN                           : 48575443A1B2C3D4 (HWTC-A1B2C3D4)
  Control flag                 : active
  Run state                    : offline
  Config state                 : normal
  Match state                  : match
  DBA type                     : -
  ONT distance(m)              : -
  ONT battery state            : not support
  Memory occupation            : -
  CPU occupation               : -
  Temperature                  : -
  Authentic type               : SN-auth
  SNMP profile ID              : -
  SNMP profile name            : -
  Management mode              : OMCI
  Software work mode           : normal
  Isolation state              : normal
  ONT IP 0 address/mask        : -
  Description                  : CLIENTE_JOAO_RUA_10
  Last down cause              : dying-gasp
  Last up time                 : 2024-01-10 10:20:30
  Last down time               : 2024-02-15 15:40:12
  Last dying gasp time         : 2024-02-15 15:40:12
  ONT online duration          : -
  ONT offline duration         : 45 day(s), 12 hour(s), 30 minute(s), 0 second(s)
  -----------------------------------------------------------------------------
"""

SAMPLE_ONT_DETAIL_DATETIME_ONLY = """
  -----------------------------------------------------------------------------
  ONT-ID                       : 3
  SN                           : 4857544399887766
  Description                  : EMPRESA_XYZ_FIBRA
  Run state                    : offline
  Config state                 : normal
  Last down cause              : loss of signal
  Last up time                 : 2024-04-01 08:00:00
  Last down time               : 2024-04-10 12:00:00
  ONT offline duration         : -
  -----------------------------------------------------------------------------
"""

SAMPLE_CURRENT_CONFIG = """
[ont-configuration]
<ont-configuration>
ont add 0 1 sn-auth "48575443A1B2C3D4" omci ont-lineprofile-id 20 ont-srvprofile-id 20 desc "CLIENTE_JOAO_RUA_10"
ont port native-vlan 0 1 eth 1 vlan 100 priority 0
#
[service-port]
<service-port>
service-port 1050 vlan 100 gpon 0/1/0 ont 1 gemport 1 multi-service user-vlan 100 tag-transform translate
service-port 1051 vlan 200 gpon 0/1/0 ont 1 gemport 2 multi-service user-vlan 200 tag-transform translate
#
return
"""

SAMPLE_SERVICE_PORT_TABLE = """
  INDEX VLAN VLAN  PORT F/ S/ P  ONT GEM  FLOW  FLOW       RX TX  STATE
        ID   ATTR  TYPE          ID  PORT TYPE  PARA
  ---------------------------------------------------------------------
  1050  100  common gpon 0/1 /0  1   1    vlan  100        -  -   down
  1051  200  common gpon 0/1 /0  1   2    vlan  200        -  -   down
  ---------------------------------------------------------------------
  Total: 2
"""


def test_parse_board_slots():
    slots = parse_board_slots(SAMPLE_BOARD_OUTPUT)
    assert slots == [1, 2]


def test_parse_ont_summary():
    onts = parse_ont_summary(SAMPLE_ONT_SUMMARY, frame=0, slot=1, port=0)
    assert len(onts) == 4
    assert onts[0].ont_id == 0
    assert onts[0].run_state == "online"
    assert onts[1].ont_id == 1
    assert onts[1].run_state == "offline"
    assert onts[3].ont_id == 3
    assert onts[3].run_state == "offline"


def test_parse_ont_detail_with_duration():
    ont = OntRecord(frame=0, slot=1, port=0, ont_id=1, run_state="offline")
    ref_time = datetime(2024, 4, 1, 12, 0, 0)
    enriched = parse_ont_detail(SAMPLE_ONT_DETAIL_WITH_DURATION, ont, reference_time=ref_time)

    assert enriched.sn == "48575443A1B2C3D4 (HWTC-A1B2C3D4)"
    assert enriched.description == "CLIENTE_JOAO_RUA_10"
    assert enriched.last_down_cause == "dying-gasp"
    assert enriched.last_down_time == datetime(2024, 2, 15, 15, 40, 12)
    assert enriched.calculated_offline_days == pytest.approx(45.52, 0.05)


def test_parse_ont_detail_datetime_only():
    ont = OntRecord(frame=0, slot=1, port=0, ont_id=3, run_state="offline")
    ref_time = datetime(2024, 4, 20, 12, 0, 0)  # 10 days after 2024-04-10 12:00:00
    enriched = parse_ont_detail(SAMPLE_ONT_DETAIL_DATETIME_ONLY, ont, reference_time=ref_time)

    assert enriched.sn == "4857544399887766"
    assert enriched.description == "EMPRESA_XYZ_FIBRA"
    assert enriched.calculated_offline_days == pytest.approx(10.0, 0.01)


def test_parse_service_ports_from_config():
    sp_ids = parse_service_ports_from_config(SAMPLE_CURRENT_CONFIG)
    assert sp_ids == [1050, 1051]


def test_parse_service_ports_from_table():
    records = parse_service_ports_from_table(SAMPLE_SERVICE_PORT_TABLE)
    assert len(records) == 2
    assert records[0].index == 1050
    assert records[0].vlan == 100
    assert records[1].index == 1051
    assert records[1].vlan == 200


SAMPLE_ONT_SUMMARY_TWO_TABLES = """
  ------------------------------------------------------------------------------
  In port 0/1/0, the total of ONTs are: 3, online: 1, offline: 2
  ------------------------------------------------------------------------------
  ONT  Run     Last                Last                Last                   
  ID   State   UpTime              DownTime            DownCause              
  ------------------------------------------------------------------------------
  0    online  2026-01-10 08:00:00 2026-01-09 10:00:00 dying-gasp             
  1    offline 2025-11-01 09:00:00-03:00 2025-12-01 12:00:00-03:00 dying-gasp             
  2    offline -                   -                   -                      
  ------------------------------------------------------------------------------
  ONT        SN        Type          Distance Rx/Tx power  Description          
  ID                                 (m)      (dBm)                             
  ------------------------------------------------------------------------------
  0   4857544311112222 HG8245H       310      -19.20/2.10  CLIENTE_ONLINE       
  1   48575443AAAABBBB EG8145V5      -        -/-          joao.silva.login     
  2   48575443CCCCDDDD HG8310M       -        -/-          maria.nunca.subiu    
  ------------------------------------------------------------------------------
"""


def test_parse_ont_summary_two_tables_with_tz():
    ref_time = datetime(2026, 1, 15, 12, 0, 0)
    onts = parse_ont_summary(
        SAMPLE_ONT_SUMMARY_TWO_TABLES,
        frame=0,
        slot=1,
        port=0,
        reference_time=ref_time,
    )
    assert len(onts) == 3
    assert onts[1].ont_id == 1
    assert onts[1].run_state == "offline"
    assert onts[1].sn == "48575443AAAABBBB"
    assert onts[1].description == "joao.silva.login"
    assert onts[1].customer_login == "joao.silva.login"
    assert onts[1].last_down_cause == "dying-gasp"
    assert onts[1].calculated_offline_days == pytest.approx(45.0, 0.01)

    assert onts[2].ont_id == 2
    assert onts[2].run_state == "offline"
    assert onts[2].sn == "48575443CCCCDDDD"
    assert onts[2].description == "maria.nunca.subiu"
    assert onts[2].calculated_offline_days is None


