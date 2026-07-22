#!/usr/bin/env python3
"""
Get full diaginfo from Zabbix Server and all proxies via Task Manager API.
Usage: python3 zbx_diaginfo.py [--url URL] [--user USER] [--password PASSWORD] [--no-proxies]
"""

import argparse
import json
import sys
import time

from typing import Dict, List

import requests
from pyzabbix import ZabbixAPI

POLL_INTERVAL = 2   # seconds between task.get polls
POLL_TIMEOUT  = 60  # seconds before giving up

# ── Zabbix 6 ──────────────────────────────────────────────────────────────────

# Секции, поддерживаемые сервером (Zabbix 6)
DIAGINFO_REQUEST_SERVER_V6 = {
    "historycache": {
        "stats": "extend",
        "top":   {"values": 25}
    },
    "valuecache": {
        "stats": "extend",
        "top":   {"values": 25, "request.values": 25}
    },
    "preprocessing": {
        "stats": "extend"
    },
    "alerting": {
        "stats": "extend",
        "top":   {"media.alerts": 25, "source.alerts": 25}
    },
    "lld": {
        "stats": "extend",
        "top":   {"values": 25}
    }
}

# Прокси поддерживает только historycache и preprocessing (Zabbix 6)
DIAGINFO_REQUEST_PROXY_V6 = {
    "historycache": {
        "stats": "extend",
        "top":   {"values": 25}
    },
    "preprocessing": {
        "stats": "extend"
    }
}

# ── Zabbix 7 ──────────────────────────────────────────────────────────────────
# preprocessing получил top-поля; добавлена секция connector.
# Параметр proxyid (вместо proxy_hostid в Zabbix 6).

# Секции, поддерживаемые сервером (Zabbix 7)
DIAGINFO_REQUEST_SERVER_V7 = {
    "historycache": {
        "stats": "extend",
        "top":   {"values": 25}
    },
    "valuecache": {
        "stats": "extend",
        "top":   {"values": 25, "request.values": 25}
    },
    "preprocessing": {
        "stats": "extend",
        "top":   {
            "peak":       25,
            "sequences":  25,
            "values_num": 25,
            "values_sz":  25,
            "time_ms":    25,
            "total_ms":   25
        }
    },
    "alerting": {
        "stats": "extend",
        "top":   {"media.alerts": 25, "source.alerts": 25}
    },
    "lld": {
        "stats": "extend",
        "top":   {"values": 25}
    },
    "connector": {
        "stats": "extend",
        "top":   {"values": 25}
    }
}

# Прокси поддерживает только historycache и preprocessing (Zabbix 7)
DIAGINFO_REQUEST_PROXY_V7 = {
    "historycache": {
        "stats": "extend",
        "top":   {"values": 25}
    },
    "preprocessing": {
        "stats": "extend",
        "top":   {
            "peak":       25,
            "sequences":  25,
            "values_num": 25,
            "values_sz":  25,
            "time_ms":    25,
            "total_ms":   25
        }
    }
}


def parse_args():
    p = argparse.ArgumentParser(description="Fetch Zabbix Server and proxies diaginfo via task API")
    p.add_argument("--url",        default="http://localhost/zabbix", help="Zabbix frontend URL")
    p.add_argument("--user",       default="Admin")
    p.add_argument("--password",   default="zabbix")
    p.add_argument("--no-proxies", action="store_true", help="Collect diaginfo only from server")
    p.add_argument("--proxy",      action="append", dest="proxies", metavar="NAME",
                   help="Собрать данные только с указанных прокси (можно указать несколько раз). "
                        "Сравнение без учёта регистра, поддерживается частичное совпадение.")
    mtls = p.add_argument_group("mTLS")
    mtls.add_argument("--mtls",      action="store_true", default=False,
                      help="Включить mTLS (по умолчанию выключено)")
    mtls.add_argument("--cert",      metavar="PATH", help="Клиентский сертификат (PEM)")
    mtls.add_argument("--key",       metavar="PATH", help="Приватный ключ клиента (PEM)")
    mtls.add_argument("--ca",        metavar="PATH", help="CA-сертификат для проверки сервера (PEM)")
    return p.parse_args()


def connect(args):
    """Создаёт подключение к Zabbix API, опционально с mTLS.
    Возвращает (zapi, major_version: int)."""
    session = requests.Session()
    if args.mtls:
        if not args.cert or not args.key:
            print("[!] --mtls требует --cert и --key", file=sys.stderr)
            sys.exit(1)
        session.cert = (args.cert, args.key)
        session.verify = args.ca if args.ca else True
    zapi = ZabbixAPI(args.url, session=session)
    zapi.login(args.user, args.password)
    major = int(zapi.api_version().split(".")[0])
    print(f"[*] Zabbix API version: {zapi.api_version()} (major={major})", file=sys.stderr)
    return zapi, major


def get_proxies(zapi, major: int) -> List[Dict]:
    """Возвращает список прокси [{proxyid, name}, ...]. При ошибке — []."""
    try:
        output = ["proxyid", "name"] if major >= 7 else ["proxyid", "host", "name"]
        raw = zapi.proxy.get(output=output)
        result = []
        for p in raw:
            name = p.get("name") or p.get("host") or f"proxy-{p['proxyid']}"
            result.append({"proxyid": p["proxyid"], "name": name})
        return result
    except Exception as e:
        print(f"[!] Не удалось получить список прокси: {e}", file=sys.stderr)
        return []


def create_tasks(zapi, proxies: List[Dict], major: int) -> Dict:
    """
    Создаёт задачи diaginfo для сервера и всех прокси.
    Возвращает {taskid: {"type": "server"|"proxy", "proxyid": str, "name": str}}.
    """
    task_map = {}

    if major >= 7:
        proxy_id_field      = "proxyid"
        server_request      = DIAGINFO_REQUEST_SERVER_V7
        proxy_request       = DIAGINFO_REQUEST_PROXY_V7
        server_proxy_id_val = 0
    else:
        proxy_id_field      = "proxy_hostid"
        server_request      = DIAGINFO_REQUEST_SERVER_V6
        proxy_request       = DIAGINFO_REQUEST_PROXY_V6
        server_proxy_id_val = 0

    # Сервер
    try:
        result = zapi.do_request("task.create", [{
            "type":           1,
            proxy_id_field:   server_proxy_id_val,
            "request":        server_request
        }])["result"]
        taskid = result["taskids"][0]
        task_map[taskid] = {"type": "server", "proxyid": "0", "name": "Zabbix Server"}
        print(f"[*] Задача создана для сервера: taskid={taskid}", file=sys.stderr)
    except Exception as e:
        print(f"[!] Не удалось создать задачу для сервера: {e}", file=sys.stderr)

    # Прокси
    for proxy in proxies:
        try:
            result = zapi.do_request("task.create", [{
                "type":          1,
                proxy_id_field:  int(proxy["proxyid"]),
                "request":       proxy_request
            }])["result"]
            taskid = result["taskids"][0]
            task_map[taskid] = {
                "type":    "proxy",
                "proxyid": proxy["proxyid"],
                "name":    proxy["name"]
            }
            print(f"[*] Задача создана для прокси '{proxy['name']}': taskid={taskid}", file=sys.stderr)
        except Exception as e:
            print(f"[!] Не удалось создать задачу для прокси '{proxy['name']}': {e}", file=sys.stderr)

    return task_map


def poll_tasks(zapi, task_map: dict, deadline: float) -> dict:
    """
    Опрашивает задачи до получения всех результатов или таймаута.
    Возвращает {taskid: {"data": ...} | {"error": ...}}.
    """
    remaining = set(task_map.keys())
    results = {}

    while time.time() < deadline and remaining:
        time.sleep(POLL_INTERVAL)
        print(".", end="", flush=True, file=sys.stderr)

        try:
            tasks = zapi.task.get(
                taskids=list(remaining),
                output=["taskid", "status", "result"]
            )
        except Exception as e:
            print(f"\n[!] Ошибка при опросе задач: {e}", file=sys.stderr)
            break

        for task in tasks:
            if task["result"] is None:
                continue
            tid = task["taskid"]
            remaining.discard(tid)
            if task["result"]["status"] == -1:
                results[tid] = {"error": str(task["result"]["data"])}
            else:
                results[tid] = {"data": task["result"]["data"]}

    print(file=sys.stderr)

    for tid in remaining:
        results[tid] = {"error": "timeout"}

    return results


def build_output(task_map: dict, results: dict) -> dict:
    """Собирает итоговый JSON из task_map и results."""
    output = {"server": None, "proxies": []}

    for taskid, meta in task_map.items():
        res = results.get(taskid, {"error": "no result"})
        if meta["type"] == "server":
            if "data" in res:
                output["server"] = {"name": meta["name"], "diaginfo": res["data"]}
            else:
                output["server"] = {"name": meta["name"], "error": res["error"]}
        else:
            entry = {"proxyid": meta["proxyid"], "name": meta["name"]}
            if "data" in res:
                entry["diaginfo"] = res["data"]
            else:
                entry["error"] = res["error"]
            output["proxies"].append(entry)

    # Сортировка прокси по имени для стабильного вывода
    output["proxies"].sort(key=lambda p: p["name"].lower())

    return output


def main():
    args = parse_args()

    zapi, major = connect(args)

    proxies = []
    if not args.no_proxies:
        proxies = get_proxies(zapi, major)
        if proxies:
            print(f"[*] Найдено прокси: {len(proxies)}", file=sys.stderr)
        else:
            print("[*] Прокси не найдены или недоступны", file=sys.stderr)

        # Фильтр по имени (--proxy, частичное совпадение без учёта регистра)
        if args.proxies:
            filters = [f.lower() for f in args.proxies]
            before = len(proxies)
            proxies = [p for p in proxies if any(f in p["name"].lower() for f in filters)]
            print(f"[*] После фильтра --proxy: {len(proxies)} из {before}", file=sys.stderr)

    task_map = create_tasks(zapi, proxies, major)

    if not task_map:
        print("[!] Не удалось создать ни одной задачи.", file=sys.stderr)
        zapi.user.logout()
        sys.exit(1)

    print(f"[*] Ожидание результатов (таймаут {POLL_TIMEOUT}с)...", file=sys.stderr)
    deadline = time.time() + POLL_TIMEOUT
    results = poll_tasks(zapi, task_map, deadline)

    output = build_output(task_map, results)

    print(json.dumps(output, indent=2, ensure_ascii=False))

    zapi.user.logout()


if __name__ == "__main__":
    main()
