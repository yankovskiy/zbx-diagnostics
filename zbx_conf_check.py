#!/usr/bin/env python3
#
# Проверка zabbix_server.conf: обязательные параметры, оценка памяти кэшей,
# оценка количества соединений с базой данных.
#
# Поддерживаются Zabbix 6 и Zabbix 7 (выбор версии — параметр командной
# строки --zabbix-version, по умолчанию 6).
#
# Значения по умолчанию и поведение процессов взяты из исходного кода сервера
# Zabbix соответствующей версии: дефолты параметров и правила разбора конфига —
# src/zabbix_server/server.c (массив cfg[], массив config_forks[],
# zbx_set_defaults(); а также src/libs/zbxconf/cfg.c), соединения с БД — вызовы
# zbx_db_connect() в процессах src/zabbix_server/ и src/libs/.

import argparse
import sys

KILO = 1024
MEGA = 1024 ** 2
GIGA = 1024 ** 3
TEBI = 1024 ** 4

SIZE_SUFFIXES = {"K": KILO, "M": MEGA, "G": GIGA, "T": TEBI}

# Кэши в разделяемой памяти: (параметр, условие выделения; None — выделяется
# всегда). Поведение одинаково для Zabbix 6 и 7: zbx_tfc_init() и zbx_vc_init()
# не выделяют память при размере 0, zbx_vmware_init() вызывается только при
# StartVMwareCollectors > 0 (server.c: server_startup()).
CACHES = [
    ("CacheSize", None),
    ("HistoryCacheSize", None),
    ("HistoryIndexCacheSize", None),
    ("TrendCacheSize", None),
    ("TrendFunctionCacheSize", lambda eff: eff["TrendFunctionCacheSize"] > 0),
    ("ValueCacheSize", lambda eff: eff["ValueCacheSize"] > 0),
    ("VMwareCacheSize", lambda eff: eff["StartVMwareCollectors"] > 0),
]

BYTE_PARAMS = frozenset(name for name, _ in CACHES)

CACHE_SKIP_REASON = {
    "TrendFunctionCacheSize": "TrendFunctionCacheSize=0 — кэш выключен",
    "ValueCacheSize": "ValueCacheSize=0 — кэш выключен",
    "VMwareCacheSize": "выделяется только при StartVMwareCollectors>0",
}

# ---------------------------------------------------------------------------
# Zabbix 6
# ---------------------------------------------------------------------------

# Параметры, которые должны быть явно заданы в файле конфигурации.
# Отредактируйте список по своему усмотрению.
REQUIRED_PARAMS_V6 = [
    "EnableSystemAudit",
    "TrendFunctionCacheSize",
]

# Значения параметров по умолчанию (src/zabbix_server/server.c,
# src/libs/zbxaudit/audit_log.c).
DEFAULTS_V6 = {
    # количество форков процессов
    "StartPollers": 5,
    "StartPollersUnreachable": 1,
    "StartIPMIPollers": 0,
    "StartPreprocessors": 3,
    "StartHistoryPollers": 5,
    "StartTrappers": 5,
    "StartPingers": 1,
    "StartDiscoverers": 1,
    "StartHTTPPollers": 1,
    "StartTimers": 1,
    "StartEscalators": 1,
    "StartAlerters": 3,
    "StartJavaPollers": 0,
    "StartVMwareCollectors": 0,
    "StartSNMPTrapper": 0,
    "StartProxyPollers": 1,
    "StartDBSyncers": 4,
    "StartLLDProcessors": 2,
    "StartReportWriters": 0,
    "StartODBCPollers": 1,
    "HousekeepingFrequency": 1,
    # кэши в разделяемой памяти, байты
    "CacheSize": 32 * MEGA,
    "HistoryCacheSize": 16 * MEGA,
    "HistoryIndexCacheSize": 4 * MEGA,
    "TrendCacheSize": 4 * MEGA,
    "TrendFunctionCacheSize": 4 * MEGA,
    "ValueCacheSize": 8 * MEGA,
    "VMwareCacheSize": 8 * MEGA,
    # аудит (src/libs/zbxaudit/audit_log.c)
    "EnableSystemAudit": 1,
    "EnableSystemAuditToDb": 1,
    "EnableSystemAuditToFile": 0,
}

# Все параметры, известные zabbix_server (массив cfg[] в
# src/zabbix_server/server.c). Используется для предупреждений об опечатках.
KNOWN_PARAMS_V6 = frozenset({
    "ListenPort", "SourceIP", "LogType", "LogFile", "LogFileSize", "DebugLevel",
    "PidFile", "SocketDir", "DBHost", "DBName", "DBSchema", "DBUser",
    "DBPassword", "DBSocket", "DBPort", "AllowUnsupportedDBVersions",
    "DBTLSConnect", "DBTLSCertFile", "DBTLSKeyFile", "DBTLSCAFile",
    "DBTLSCipher", "DBTLSCipher13", "HistoryStorageURL", "HistoryStorageTypes",
    "HistoryStorageDateIndex", "ExportDir", "ExportType", "ExportFileSize",
    "StartPollers", "StartIPMIPollers", "StartPreprocessors",
    "StartPollersUnreachable", "StartHistoryPollers", "StartTrappers",
    "StartPingers", "StartDiscoverers", "StartHTTPPollers", "StartTimers",
    "StartEscalators", "StartAlerters", "JavaGateway", "JavaGatewayPort",
    "StartJavaPollers", "StartVMwareCollectors", "VMwareFrequency",
    "VMwarePerfFrequency", "VMwareCacheSize", "VMwareTimeout",
    "SNMPTrapperFile", "StartSNMPTrapper", "ListenIP", "HousekeepingFrequency",
    "MaxHousekeeperDelete", "CacheSize", "CacheUpdateFrequency",
    "StartDBSyncers", "HistoryCacheSize", "HistoryIndexCacheSize",
    "TrendCacheSize", "TrendFunctionCacheSize", "ValueCacheSize", "Timeout",
    "TrapperTimeout", "UnreachablePeriod", "UnavailableDelay",
    "UnreachableDelay", "AlertScriptsPath", "ExternalScripts",
    "FpingLocation", "Fping6Location", "SSHKeyLocation", "LogSlowQueries",
    "TmpDir", "StartProxyPollers", "ProxyConfigFrequency",
    "ProxyDataFrequency", "StartLLDProcessors", "AllowRoot", "User",
    "Include", "SSLCertLocation", "SSLKeyLocation", "SSLCALocation",
    "StatsAllowedIP", "LoadModulePath", "LoadModule", "TLSCAFile",
    "TLSCRLFile", "TLSCertFile", "TLSKeyFile", "TLSCipherCert13",
    "TLSCipherCert", "TLSCipherPSK13", "TLSCipherPSK", "TLSCipherAll13",
    "TLSCipherAll", "VaultToken", "VaultURL", "VaultDBPath",
    "StartReportWriters", "WebServiceURL", "ServiceManagerSyncFrequency",
    "ProblemHousekeepingFrequency", "StartODBCPollers", "ListenBacklog",
    "HANodeName", "NodeAddress", "EnableSystemAudit", "EnableSystemAuditToDb",
    "EnableSystemAuditToFile", "SystemAuditLogFile", "EnableLuhn",
    "LuhnNonDelimiter",
})

# Процессы, держащие соединение с базой данных (1 соединение на экземпляр):
# (имя процесса, параметр конфига или None для фиксированных процессов,
#  источник в коде).
DB_CONN_PROCESSES_V6 = [
    ("configuration syncer", None, "dbconfig/dbconfig.c"),
    ("history syncer", "StartDBSyncers", "dbsyncer/dbsyncer.c"),
    ("escalator", "StartEscalators", "escalator/escalator.c"),
    ("timer", "StartTimers", "timer/timer.c"),
    ("discoverer", "StartDiscoverers", "discoverer/discoverer.c"),
    ("http poller", "StartHTTPPollers", "httppoller/httppoller.c"),
    ("trapper", "StartTrappers", "trapper/trapper.c"),
    ("history poller", "StartHistoryPollers", "poller/poller.c"),
    ("proxy poller", "StartProxyPollers", "proxypoller/proxypoller.c"),
    ("LLD worker", "StartLLDProcessors", "lld/lld_worker.c"),
    ("alert syncer", None, "alerter/alert_syncer.c"),
    ("alert manager", None, "alerter/alert_manager.c"),
    ("task manager", None, "taskmanager/taskmanager.c"),
    ("service manager", None, "service/service_manager.c"),
    ("availability manager", None, "availability/avail_manager.c"),
    ("trigger housekeeper", None, "housekeeper/trigger_housekeeper.c"),
    ("HA manager", None, "ha/ha_manager.c"),
]

# Процессы, которые запускаются (и открывают соединение) только при ненулевом
# управляющем параметре: (имя процесса, управляющий параметр, источник).
DB_CONN_CONDITIONAL_V6 = [
    ("SNMP trapper", "StartSNMPTrapper", "snmptrapper/snmptrapper.c"),
    ("IPMI manager", "StartIPMIPollers", "ipmi/ipmi_manager.c"),
    ("report manager", "StartReportWriters", "reporter/report_manager.c"),
]

# Процессы, не открывающие соединение с базой данных Zabbix:
# (имя процесса, параметр конфига или None).
DB_NO_CONNECTIONS_V6 = [
    ("poller", "StartPollers"),
    ("unreachable poller", "StartPollersUnreachable"),
    ("icmp pinger", "StartPingers"),
    ("IPMI poller", "StartIPMIPollers"),
    ("java poller", "StartJavaPollers"),
    ("vmware collector", "StartVMwareCollectors"),
    ("alerter", "StartAlerters"),
    ("preprocessing manager", None),
    ("preprocessing worker", "StartPreprocessors"),
    ("LLD manager", None),
    ("self-monitoring", None),
    ("ODBC poller", "StartODBCPollers"),
    ("report writer", "StartReportWriters"),
]

# ---------------------------------------------------------------------------
# Zabbix 7
# ---------------------------------------------------------------------------

# В Zabbix 7 параметры управления системным аудитом (EnableSystemAudit и др.)
# удалены из файла конфигурации, поэтому в списке обязательных их нет.
REQUIRED_PARAMS_V7 = [
    "TrendFunctionCacheSize",
]

# Значения параметров по умолчанию (src/zabbix_server/server.c: массив
# config_forks[], zbx_set_defaults()). По сравнению с Zabbix 6 изменены
# StartDiscoverers (1 -> 5) и StartPreprocessors (3 -> 16), добавлены новые
# типы poller'ов и StartConnectors; параметры аудита отсутствуют.
DEFAULTS_V7 = {
    # количество форков процессов
    "StartPollers": 5,
    "StartPollersUnreachable": 1,
    "StartIPMIPollers": 0,
    "StartPreprocessors": 16,
    "StartHistoryPollers": 5,
    "StartTrappers": 5,
    "StartPingers": 1,
    "StartDiscoverers": 5,
    "StartHTTPPollers": 1,
    "StartTimers": 1,
    "StartEscalators": 1,
    "StartAlerters": 3,
    "StartJavaPollers": 0,
    "StartVMwareCollectors": 0,
    "StartSNMPTrapper": 0,
    "StartProxyPollers": 1,
    "StartDBSyncers": 4,
    "StartLLDProcessors": 2,
    "StartReportWriters": 0,
    "StartODBCPollers": 1,
    # новые типы poller'ов (StartPollers в Zabbix 6 был разделён)
    "StartAgentPollers": 1,
    "StartSNMPPollers": 1,
    "StartHTTPAgentPollers": 1,
    "StartBrowserPollers": 1,
    # коннекторы (появились в Zabbix 7)
    "StartConnectors": 0,
    "HousekeepingFrequency": 1,
    # кэши в разделяемой памяти, байты (значения те же, что и в Zabbix 6)
    "CacheSize": 32 * MEGA,
    "HistoryCacheSize": 16 * MEGA,
    "HistoryIndexCacheSize": 4 * MEGA,
    "TrendCacheSize": 4 * MEGA,
    "TrendFunctionCacheSize": 4 * MEGA,
    "ValueCacheSize": 8 * MEGA,
    "VMwareCacheSize": 8 * MEGA,
}

# Все параметры, известные zabbix_server 7 (массив cfg[] в
# src/zabbix_server/server.c). Используется для предупреждений об опечатках.
KNOWN_PARAMS_V7 = frozenset({
    "ListenPort", "SourceIP", "LogType", "LogFile", "LogFileSize", "DebugLevel",
    "PidFile", "SocketDir", "DBHost", "DBName", "DBSchema", "DBUser",
    "DBPassword", "DBSocket", "DBPort", "AllowUnsupportedDBVersions",
    "DBTLSConnect", "DBTLSCertFile", "DBTLSKeyFile", "DBTLSCAFile",
    "DBTLSCipher", "DBTLSCipher13", "HistoryStorageURL", "HistoryStorageTypes",
    "HistoryStorageDateIndex", "ExportDir", "ExportType", "ExportFileSize",
    "StartPollers", "StartIPMIPollers", "StartPreprocessors",
    "StartPollersUnreachable", "StartHistoryPollers", "StartTrappers",
    "StartPingers", "StartDiscoverers", "StartHTTPPollers", "StartTimers",
    "StartEscalators", "StartAlerters", "JavaGateway", "JavaGatewayPort",
    "StartJavaPollers", "StartVMwareCollectors", "VMwareFrequency",
    "VMwarePerfFrequency", "VMwareCacheSize", "VMwareTimeout",
    "SNMPTrapperFile", "StartSNMPTrapper", "ListenIP", "HousekeepingFrequency",
    "MaxHousekeeperDelete", "CacheSize", "CacheUpdateFrequency",
    "StartDBSyncers", "HistoryCacheSize", "HistoryIndexCacheSize",
    "TrendCacheSize", "TrendFunctionCacheSize", "ValueCacheSize", "Timeout",
    "TrapperTimeout", "UnreachablePeriod", "UnavailableDelay",
    "UnreachableDelay", "AlertScriptsPath", "ExternalScripts",
    "FpingLocation", "Fping6Location", "SSHKeyLocation", "LogSlowQueries",
    "TmpDir", "StartProxyPollers", "ProxyConfigFrequency",
    "ProxyDataFrequency", "StartLLDProcessors", "AllowRoot", "User",
    "Include", "SSLCertLocation", "SSLKeyLocation", "SSLCALocation",
    "StatsAllowedIP", "LoadModulePath", "LoadModule", "TLSCAFile",
    "TLSCRLFile", "TLSCertFile", "TLSKeyFile", "TLSCipherCert13",
    "TLSCipherCert", "TLSCipherPSK13", "TLSCipherPSK", "TLSCipherAll13",
    "TLSCipherAll", "VaultToken", "Vault", "VaultTLSCertFile", "VaultTLSKeyFile",
    "VaultURL", "VaultPrefix", "VaultDBPath",
    "StartReportWriters", "WebServiceURL", "ServiceManagerSyncFrequency",
    "ProblemHousekeepingFrequency", "StartODBCPollers", "ListenBacklog",
    "HANodeName", "NodeAddress", "EnableLuhn", "LuhnNonDelimiter",
    # новые параметры Zabbix 7
    "StartConnectors", "StartAgentPollers", "StartSNMPPollers",
    "StartHTTPAgentPollers", "StartBrowserPollers",
    "MaxConcurrentChecksPerPoller", "VPSLimit", "VPSOvercommitLimit",
    "EnableGlobalScripts", "AllowSoftwareUpdateCheck", "WebDriverURL",
    "SMSDevices",
})

# Процессы, держащие соединение с базой данных (1 соединение на экземпляр).
# По сравнению с Zabbix 6 добавлены configuration syncer worker и proxy group
# manager; discoverer вынесен в условные процессы (discovery manager).
DB_CONN_PROCESSES_V7 = [
    ("configuration syncer", None, "dbconfig/dbconfig_server.c"),
    ("configuration syncer worker", None, "dbconfigworker/dbconfigworker.c"),
    ("history syncer", "StartDBSyncers", "libs/zbxdbsyncer/dbsyncer.c"),
    ("escalator", "StartEscalators", "escalator/escalator.c"),
    ("timer", "StartTimers", "timer/timer.c"),
    ("http poller", "StartHTTPPollers", "libs/zbxhttppoller/httppoller.c"),
    ("trapper", "StartTrappers", "libs/zbxtrapper/trapper.c"),
    ("history poller", "StartHistoryPollers", "libs/zbxpoller/poller_thread.c"),
    ("proxy poller", "StartProxyPollers", "proxypoller/proxypoller.c"),
    ("LLD worker", "StartLLDProcessors", "lld/lld_worker.c"),
    ("alert syncer", None, "libs/zbxalerter/alert_syncer.c"),
    ("alert manager", None, "libs/zbxalerter/alert_manager.c"),
    ("task manager", None, "taskmanager/taskmanager_server.c"),
    ("service manager", None, "service/service_manager.c"),
    ("availability manager", None, "libs/zbxavailability/avail_manager.c"),
    ("trigger housekeeper", None, "housekeeper/trigger_housekeeper.c"),
    ("proxy group manager", None, "pgmanager/pg_manager.c"),
    ("HA manager", None, "ha/ha_manager.c"),
]

# Процессы, которые запускаются (и открывают соединение) только при ненулевом
# управляющем параметре: (имя процесса, управляющий параметр, источник).
# Discovery manager держит одно соединение на процесс; количество воркеров
# (потоков без собственного соединения) задаётся StartDiscoverers.
DB_CONN_CONDITIONAL_V7 = [
    ("discovery manager", "StartDiscoverers", "libs/zbxdiscoverer/discoverer.c"),
    ("SNMP trapper", "StartSNMPTrapper", "libs/zbxsnmptrapper/snmptrapper.c"),
    ("IPMI manager", "StartIPMIPollers", "libs/zbxipmi/ipmi_manager.c"),
    ("report manager", "StartReportWriters", "reporter/report_manager.c"),
]

# Процессы, не открывающие соединение с базой данных Zabbix:
# (имя процесса, параметр конфига или None).
DB_NO_CONNECTIONS_V7 = [
    ("poller", "StartPollers"),
    ("unreachable poller", "StartPollersUnreachable"),
    ("agent poller", "StartAgentPollers"),
    ("snmp poller", "StartSNMPPollers"),
    ("http agent poller", "StartHTTPAgentPollers"),
    ("internal poller", None),
    ("browser poller", "StartBrowserPollers"),
    ("icmp pinger", "StartPingers"),
    ("IPMI poller", "StartIPMIPollers"),
    ("java poller", "StartJavaPollers"),
    ("vmware collector", "StartVMwareCollectors"),
    ("alerter", "StartAlerters"),
    ("preprocessing manager", None),
    ("preprocessing worker", "StartPreprocessors"),
    ("LLD manager", None),
    ("self-monitoring", None),
    ("ODBC poller", "StartODBCPollers"),
    ("report writer", "StartReportWriters"),
    ("connector manager", None),
    ("connector worker", "StartConnectors"),
    ("discovery worker", "StartDiscoverers"),
]

# Профили по версиям Zabbix.
PROFILES = {
    "6": {
        "label": "Zabbix 6",
        "required": REQUIRED_PARAMS_V6,
        "defaults": DEFAULTS_V6,
        "known": KNOWN_PARAMS_V6,
        "db_conn": DB_CONN_PROCESSES_V6,
        "db_conn_cond": DB_CONN_CONDITIONAL_V6,
        "db_no_conn": DB_NO_CONNECTIONS_V6,
    },
    "7": {
        "label": "Zabbix 7",
        "required": REQUIRED_PARAMS_V7,
        "defaults": DEFAULTS_V7,
        "known": KNOWN_PARAMS_V7,
        "db_conn": DB_CONN_PROCESSES_V7,
        "db_conn_cond": DB_CONN_CONDITIONAL_V7,
        "db_no_conn": DB_NO_CONNECTIONS_V7,
    },
}


def parse_size(text):
    """'32M' -> 33554432.

    Zabbix допускает суффиксы K/M/G/T (двоичные, suffix2factor() из
    src/libs/zbxcommon/misc.c), в том числе для целых параметров.
    """
    text = text.strip()
    if not text:
        raise ValueError("пустое значение")
    if text[-1] in SIZE_SUFFIXES:
        return int(text[:-1]) * SIZE_SUFFIXES[text[-1]]
    return int(text)


def parse_config(path, known_params):
    """Разбор конфига по правилам src/libs/zbxconf/cfg.c.

    Возвращает (params, warnings): params — словарь параметр -> значение
    (строка); при дубликатах побеждает последнее значение, как в Zabbix.
    Include= не обрабатывается — только предупреждение.
    """
    params = {}
    warnings = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                warnings.append(f"строка {lineno}: ожидалось Параметр=Значение, пропущено: {line!r}")
                continue

            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip()

            if name == "Include":
                warnings.append(
                    f"строка {lineno}: Include= не обрабатывается — значения из включаемых файлов не учтены"
                )
                continue
            if name not in known_params:
                warnings.append(
                    f"строка {lineno}: неизвестный параметр {name!r} (zabbix_server с таким конфигом не запустится)"
                )

            params[name] = value

    return params, warnings


def effective_values(params, warnings, defaults):
    """Действующие числовые значения: из конфига, при отсутствии — по умолчанию."""
    values = {}
    for name, default in defaults.items():
        if name not in params:
            values[name] = default
            continue
        try:
            values[name] = parse_size(params[name])
        except ValueError:
            warnings.append(
                f"параметр {name}: не удалось разобрать значение {params[name]!r}, используется значение по умолчанию"
            )
            values[name] = default
    return values


def fmt_bytes(value):
    units = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.4g} {unit}"
        size /= 1024


def fmt_default(name, value):
    if name in BYTE_PARAMS:
        return fmt_bytes(value)
    return str(value)


def print_table(headers, rows, indent="  "):
    rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells):
        return indent + "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()

    print(fmt(headers))
    print(indent + "  ".join("-" * width for width in widths))
    for row in rows:
        print(fmt(row))


def report_required(params, profile):
    print("=" * 72)
    print("1. Проверка обязательных параметров (REQUIRED_PARAMS)")
    print("=" * 72)

    required = profile["required"]
    defaults = profile["defaults"]

    missing = []
    for name in required:
        if name in params:
            print(f"  [OK]   {name} = {params[name]}")
        else:
            note = ""
            if name in defaults:
                note = f" (по умолчанию: {fmt_default(name, defaults[name])})"
            print(f"  [FAIL] {name} — не задан{note}")
            missing.append(name)

    print()
    if missing:
        print(f"  Не задано {len(missing)} из {len(required)}: {', '.join(missing)}")
    else:
        print(f"  Все обязательные параметры заданы ({len(required)}).")
    print()
    return missing


def report_memory(params, eff):
    print("=" * 72)
    print("2. Оценка памяти кэшей (разделяемая память)")
    print("=" * 72)

    rows = []
    total = 0
    for name, condition in CACHES:
        value = eff[name]
        source = "конфиг" if name in params else "дефолт"
        if condition is not None and not condition(eff):
            rows.append((name, fmt_bytes(value), source, f"не выделяется ({CACHE_SKIP_REASON[name]})"))
            continue
        total += value
        rows.append((name, fmt_bytes(value), source, "выделяется"))

    print_table(("Параметр", "Значение", "Источник", "Статус"), rows)
    print()
    print(f"  Итого может быть выделено: {fmt_bytes(total)} ({total} байт)")
    print("  Учтены только кэши, настраиваемые через файл конфигурации.")
    print()


def report_db_connections(params, eff, profile):
    print("=" * 72)
    print("3. Оценка количества соединений с базой данных")
    print("=" * 72)
    print("  Каждый перечисленный процесс открывает 1 соединение (вызов zbx_db_connect()).")

    entries = []
    for name, param, source in profile["db_conn"]:
        if param is None:
            entries.append((name, None, "—", 1, source))
        else:
            instances = eff[param]
            entries.append((name, param, f"{param}={instances}", instances, source))
    for name, param, source in profile["db_conn_cond"]:
        instances = 1 if eff[param] > 0 else 0
        entries.append((name, param, f"{param}={eff[param]}", instances, source))

    in_config = [e for e in entries if e[1] is not None and e[1] in params]
    defaulted = [e for e in entries if e[1] is not None and e[1] not in params]
    fixed = [e for e in entries if e[1] is None]

    total = 0
    sections = (
        ("3.1. Параметры, явно заданные в конфиге", in_config),
        ("3.2. Параметры, отсутствующие в конфиге (использованы значения по умолчанию)", defaulted),
        ("3.3. Фиксированные процессы (параметра в конфиге нет)", fixed),
    )
    for title, group in sections:
        print()
        print(f"  {title}:")
        if not group:
            print("    (нет)")
            continue
        rows = []
        for name, param, param_repr, instances, source in group:
            total += instances
            rows.append((name, param_repr, instances, instances, source))
        print_table(
            ("Процесс", "Параметр", "Экземпляров", "Соединений", "Источник в коде"),
            rows,
            indent="    ",
        )

    print()
    print(f"  Итого постоянных соединений: {total}")
    print()
    print("  Дополнительно:")
    hk_freq = eff["HousekeepingFrequency"]
    freq_note = f"каждые {hk_freq} ч" if hk_freq > 0 else "только по runtime control"
    print(f"    - housekeeper: 1 соединение, непостоянное — открывается на время выполнения")
    print(f"      хаускипера ({freq_note}; housekeeper/housekeeper.c)")
    print("    - main process: соединение только на время старта (проверка версии БД,")
    print("      post-init задачи), затем закрывается (server.c)")
    print()
    print("  Процессы, не открывающие соединение с БД Zabbix:")
    rows = []
    for name, param in profile["db_no_conn"]:
        if param is None:
            rows.append((name, "—", 1))
        else:
            rows.append((name, f"{param}={eff[param]}", eff[param]))
    print_table(("Процесс", "Параметр", "Экземпляров"), rows, indent="    ")
    print()
    print("  Примечание: ODBC poller подключается к источникам данных ODBC, а не к БД Zabbix.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Проверка zabbix_server.conf: обязательные параметры, память кэшей, соединения с БД."
    )
    parser.add_argument("config", help="Путь к файлу zabbix_server.conf")
    parser.add_argument(
        "--zabbix-version",
        choices=sorted(PROFILES),
        default="6",
        help="Версия Zabbix, для которой проверяется конфиг (по умолчанию 6)",
    )
    args = parser.parse_args()

    profile = PROFILES[args.zabbix_version]

    try:
        params, warnings = parse_config(args.config, profile["known"])
    except OSError as e:
        print(f"Ошибка: не удалось прочитать конфиг: {e}", file=sys.stderr)
        return 1

    eff = effective_values(params, warnings, profile["defaults"])

    print()
    print(f"Файл конфигурации: {args.config}")
    print(f"Версия Zabbix: {profile['label']}")
    print(f"Задано параметров: {len(params)}")

    if warnings:
        print()
        print("Предупреждения:")
        for warning in warnings:
            print(f"  - {warning}")
    print()

    missing = report_required(params, profile)
    report_memory(params, eff)
    report_db_connections(params, eff, profile)

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
