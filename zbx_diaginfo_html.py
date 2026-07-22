#!/usr/bin/env python3
"""
Визуализация вывода zbx_diaginfo.py в HTML-отчёт (один файл, без зависимостей).
Поддерживает как новый формат (server + proxies), так и старый плоский JSON.
Использование: python3 zbx_diaginfo.py --url ... | python3 zbx_diaginfo_html.py > report.html
"""

import json
import re
import sys
from datetime import datetime
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def fmt(v) -> str:
    try:
        return f"{int(v):,}".replace(",", "\u202f")
    except (TypeError, ValueError):
        return str(v)


def pct(used: int, total: int) -> Optional[float]:
    if total == 0:
        return None
    return used / total * 100


def pct_class(p: float) -> str:
    if p >= 90:
        return "crit"
    if p >= 70:
        return "warn"
    return "ok"


def bytes_human(b: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} ТБ"


def make_page_id(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# ---------------------------------------------------------------------------
# HTML-строители
# ---------------------------------------------------------------------------

def gauge_html(used: int, total: int) -> str:
    if total == 0:
        return ""
    p = pct(used, total)
    cls = pct_class(p)
    return f"""<div class="gauge-wrap">
      <div class="gauge-bar"><div class="gauge-fill {cls}" style="width:{p:.1f}%"></div></div>
      <span class="gauge-label {cls}">{p:.1f}%</span>
    </div>"""


def memory_card(mem: dict) -> str:
    def render_block(label: str, block: dict) -> str:
        size = block.get("size", {})
        chunks = block.get("chunks", {})
        free = size.get("free", 0)
        used = size.get("used", 0)
        total = free + used
        rows = ""
        rows += f"<tr><td>Всего</td><td>{bytes_human(total)}</td></tr>"
        rows += f"<tr><td>Используется</td><td>{bytes_human(used)}</td></tr>"
        rows += f"<tr><td>Свободно</td><td>{bytes_human(free)}</td></tr>"
        if chunks:
            rows += f"<tr><td>Чанков занято</td><td>{fmt(chunks.get('used', 0))}</td></tr>"
            rows += f"<tr><td>Чанков свободно</td><td>{fmt(chunks.get('free', 0))}</td></tr>"
        return f"""<div class="mem-block">
          <div class="mem-block-title">{label}</div>
          {gauge_html(used, total)}
          <table class="kv-table">{rows}</table>
        </div>"""

    parts = []
    if "data" in mem and "index" in mem:
        parts.append(render_block("Данные (data)", mem["data"]))
        parts.append(render_block("Индекс (index)", mem["index"]))
    elif "size" in mem:
        parts.append(render_block("Память", mem))
    return f'<div class="mem-blocks">{"".join(parts)}</div>'


def top_table(rows: list, cols: List[Tuple[str, str, bool]], title: str) -> str:
    """cols: (field_key, Заголовок, is_id) — is_id=True выводит значение без форматирования тысяч."""
    if not rows:
        return f'<p class="empty">Нет данных</p>'
    thead = "".join(f"<th>{h}</th>" for _, h, _ in cols)
    tbody = ""
    for i, row in enumerate(rows, 1):
        cells = f"<td class='num dim'>{i}</td>"
        for k, _, is_id in cols:
            val = row.get(k, '—')
            rendered = str(val) if is_id else fmt(val)
            cells += f"<td class='num'>{rendered}</td>"
        tbody += f"<tr>{cells}</tr>"
    return f"""<div class="top-table-wrap">
      <div class="top-title">{title}</div>
      <table class="top-table">
        <thead><tr><th>#</th>{thead}</tr></thead>
        <tbody>{tbody}</tbody>
      </table>
    </div>"""


def card(title: str, body: str, icon: str = "") -> str:
    return f"""<details class="card" open>
      <summary class="card-header">{icon} {title}</summary>
      <div class="card-body">{body}</div>
    </details>"""


# ---------------------------------------------------------------------------
# Секции diaginfo
# ---------------------------------------------------------------------------

def section_historycache(data: dict) -> str:
    stats_rows = (
        f"<tr><td>Элементов (items)</td><td class='num'>{fmt(data.get('items', 0))}</td></tr>"
        f"<tr><td>Значений (values)</td><td class='num'>{fmt(data.get('values', 0))}</td></tr>"
        f"<tr><td>Время выборки</td><td class='num'>{data.get('time', 0):.6f} с</td></tr>"
    )
    mem = memory_card(data.get("memory", {}))
    top = top_table(
        data.get("top", {}).get("values", []),
        [("itemid", "itemid", True), ("values", "Значений", False)],
        "Топ элементов по количеству значений"
    )
    return card("History Cache", f"""<div class="section-grid">
      <div><table class="kv-table">{stats_rows}</table>{mem}</div>
      <div>{top}</div>
    </div>""", icon="🗄")


def section_valuecache(data: dict) -> str:
    mode_labels = {0: "нормальный", 1: "только добавление", 2: "только чтение"}
    mode = data.get("mode", 0)
    stats_rows = (
        f"<tr><td>Элементов (items)</td><td class='num'>{fmt(data.get('items', 0))}</td></tr>"
        f"<tr><td>Значений (values)</td><td class='num'>{fmt(data.get('values', 0))}</td></tr>"
        f"<tr><td>Режим</td><td>{mode_labels.get(mode, mode)}</td></tr>"
        f"<tr><td>Время выборки</td><td class='num'>{data.get('time', 0):.6f} с</td></tr>"
    )
    mem = memory_card(data.get("memory", {}))
    top_vals = top_table(
        data.get("top", {}).get("values", []),
        [("itemid", "itemid", True), ("values", "Значений", False), ("request.values", "Запросов", False)],
        "Топ по значениям"
    )
    top_req = top_table(
        data.get("top", {}).get("request.values", []),
        [("itemid", "itemid", True), ("values", "Значений", False), ("request.values", "Запросов", False)],
        "Топ по запросам"
    )
    return card("Value Cache", f"""<div class="section-grid">
      <div><table class="kv-table">{stats_rows}</table>{mem}</div>
      <div>{top_vals}{top_req}</div>
    </div>""", icon="⚡")


def section_preprocessing(data: dict) -> str:
    def badge(val, warn=100, crit=1000) -> str:
        v = int(val)
        cls = "crit" if v >= crit else ("warn" if v >= warn else "ok")
        return f'<span class="badge {cls}">{fmt(v)}</span>'

    rows = ""
    labels = {
        "values":     "Значений получено",
        "done":       "Обработано",
        "queued":     "В очереди",
        "processing": "Обрабатывается",
        "pending":    "Ожидает",
        "time":       "Время выборки",
    }
    for key, label in labels.items():
        val = data.get(key)
        if val is None:
            continue
        if key == "time":
            rows += f"<tr><td>{label}</td><td class='num'>{val:.6f} с</td></tr>"
        elif key == "queued":
            rows += f"<tr><td>{label}</td><td>{badge(val, 100, 1000)}</td></tr>"
        else:
            rows += f"<tr><td>{label}</td><td class='num'>{fmt(val)}</td></tr>"
    for key, val in data.items():
        if key not in labels:
            rows += f"<tr><td>{key}</td><td class='num'>{fmt(val)}</td></tr>"
    return card("Preprocessing", f'<table class="kv-table">{rows}</table>', icon="⚙️")


def section_alerting(data: dict) -> str:
    stats_rows = (
        f"<tr><td>Уведомлений (alerts)</td><td class='num'>{fmt(data.get('alerts', 0))}</td></tr>"
        f"<tr><td>Время выборки</td><td class='num'>{data.get('time', 0):.6f} с</td></tr>"
    )
    top_media = top_table(
        data.get("top", {}).get("media.alerts", []),
        [("mediatypeid", "mediatypeid", True), ("alerts", "Уведомлений", False)],
        "Топ по типам медиа"
    )
    top_src = top_table(
        data.get("top", {}).get("source.alerts", []),
        [("triggerid", "triggerid", True), ("alerts", "Уведомлений", False)],
        "Топ по источникам"
    )
    return card("Alerting", f"""<div class="section-grid">
      <div><table class="kv-table">{stats_rows}</table></div>
      <div>{top_media}{top_src}</div>
    </div>""", icon="🔔")


def section_lld(data: dict) -> str:
    stats_rows = (
        f"<tr><td>Правил (rules)</td><td class='num'>{fmt(data.get('rules', 0))}</td></tr>"
        f"<tr><td>Значений (values)</td><td class='num'>{fmt(data.get('values', 0))}</td></tr>"
        f"<tr><td>Время выборки</td><td class='num'>{data.get('time', 0):.6f} с</td></tr>"
    )
    top = top_table(
        data.get("top", {}).get("values", []),
        [("itemid", "itemid", True), ("values", "Значений", False)],
        "Топ элементов"
    )
    return card("LLD Cache", f"""<div class="section-grid">
      <div><table class="kv-table">{stats_rows}</table></div>
      <div>{top}</div>
    </div>""", icon="🔍")


SECTION_RENDERERS = [
    ("historycache",  section_historycache),
    ("valuecache",    section_valuecache),
    ("preprocessing", section_preprocessing),
    ("alerting",      section_alerting),
    ("lld",           section_lld),
]


# ---------------------------------------------------------------------------
# Рендер страниц (сервер / прокси)
# ---------------------------------------------------------------------------

def render_page(node: dict) -> str:
    page_id = make_page_id(node["name"])

    if "error" in node and "diaginfo" not in node:
        body = f'<div class="node-error">Нет данных: {node["error"]}</div>'
    else:
        diaginfo = node.get("diaginfo", {})
        body = "".join(
            renderer(diaginfo[key])
            for key, renderer in SECTION_RENDERERS
            if key in diaginfo
        )

    return f'<div class="page" id="page-{page_id}">{body}</div>'


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  background: #0f1117;
  color: #c9d1d9;
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
}

/* Шапка */
header {
  background: #161b22;
  border-bottom: 1px solid #30363d;
  padding: 14px 32px;
  display: flex;
  align-items: center;
  gap: 16px;
  position: sticky;
  top: 0;
  z-index: 20;
}
header h1 { font-size: 18px; font-weight: 600; color: #e6edf3; }
header .meta { font-size: 12px; color: #8b949e; margin-left: auto; }
.zabbix-logo { width: 26px; height: 26px; flex-shrink: 0; }

/* Таб-навигация */
.tab-bar {
  background: #161b22;
  border-bottom: 1px solid #30363d;
  padding: 0 16px 0 32px;
  display: flex;
  align-items: center;
  gap: 2px;
  overflow-x: auto;
  position: sticky;
  top: 57px;
  z-index: 19;
}
.tab-bar::-webkit-scrollbar { height: 3px; }
.tab-bar::-webkit-scrollbar-thumb { background: #30363d; }

/* Кастомный дропдаун для проксей */
.proxy-dropdown {
  position: relative;
  flex-shrink: 0;
  margin-left: 4px;
  padding-left: 8px;
  border-left: 1px solid #30363d;
  align-self: stretch;
  display: flex;
  align-items: center;
}
.proxy-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #8b949e;
  font-size: 13px;
  font-weight: 500;
  padding: 5px 10px;
  cursor: pointer;
  white-space: nowrap;
  transition: color .15s, border-color .15s;
  user-select: none;
}
.proxy-btn:hover { color: #c9d1d9; border-color: #484f58; }
.proxy-btn.active { color: #e6edf3; border-color: #58a6ff; }
.proxy-btn.has-error { color: #f85149; border-color: #f85149; }
.proxy-btn-arrow {
  font-size: 10px;
  color: #484f58;
  transition: transform .2s;
}
.proxy-btn.open .proxy-btn-arrow { transform: rotate(180deg); }

.proxy-panel {
  display: none;
  position: fixed;
  min-width: 220px;
  max-width: 300px;
  background: #1c2128;
  border: 1px solid #30363d;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,.5);
  z-index: 1000;
  overflow: hidden;
}
.proxy-panel.open { display: flex; flex-direction: column; }
.proxy-search-wrap {
  padding: 8px;
  border-bottom: 1px solid #30363d;
}
.proxy-search {
  width: 100%;
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #c9d1d9;
  font-size: 13px;
  padding: 5px 10px;
  outline: none;
  transition: border-color .15s;
}
.proxy-search:focus { border-color: #58a6ff; }
.proxy-search::placeholder { color: #484f58; }
.proxy-list {
  overflow-y: auto;
  max-height: 280px;
  padding: 4px 0;
}
.proxy-list::-webkit-scrollbar { width: 4px; }
.proxy-list::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
.proxy-item {
  padding: 7px 14px;
  font-size: 13px;
  color: #c9d1d9;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background .1s;
}
.proxy-item:hover { background: #21262d; }
.proxy-item.active { background: #1f3148; color: #58a6ff; }
.proxy-item.error { color: #f85149; }
.proxy-item.error.active { background: #2d0f0f; color: #f85149; }
.proxy-item.hidden { display: none; }
.proxy-empty {
  padding: 10px 14px;
  font-size: 13px;
  color: #484f58;
  font-style: italic;
}

.tab {
  padding: 10px 18px;
  font-size: 13px;
  font-weight: 500;
  color: #8b949e;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
  user-select: none;
  transition: color .15s, border-color .15s;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
}
.tab:hover { color: #c9d1d9; }
.tab.active { color: #e6edf3; border-bottom-color: #58a6ff; }
.tab.error { color: #f85149; }
.tab.error.active { border-bottom-color: #f85149; }

/* Страницы */
main { max-width: 1400px; margin: 0 auto; padding: 24px 32px; }

.page { display: none; flex-direction: column; gap: 16px; }
.page.active { display: flex; }

.node-error {
  padding: 16px;
  background: #2d0f0f;
  border: 1px solid #f85149;
  border-radius: 8px;
  color: #f85149;
}

/* Сворачиваемые карточки */
details.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  overflow: hidden;
}
details.card > summary {
  background: #1c2128;
  padding: 10px 18px;
  font-size: 15px;
  font-weight: 600;
  color: #e6edf3;
  border-bottom: 1px solid #30363d;
  cursor: pointer;
  list-style: none;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
details.card > summary::-webkit-details-marker { display: none; }
details.card > summary::after {
  content: "▾";
  color: #8b949e;
  font-size: 16px;
  transition: transform .2s;
  flex-shrink: 0;
}
details.card:not([open]) > summary::after { transform: rotate(-90deg); }
details.card > summary:hover { background: #21262d; }
.card-body { padding: 16px 18px; }

.section-grid {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 2fr;
  gap: 24px;
  align-items: start;
}

/* kv-table */
.kv-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
.kv-table td { padding: 5px 8px; border-bottom: 1px solid #21262d; }
.kv-table td:first-child { color: #8b949e; width: 55%; }
.kv-table .num { font-variant-numeric: tabular-nums; text-align: right; font-family: monospace; }
.kv-table .dim { color: #8b949e; }

/* memory */
.mem-blocks { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
.mem-block { flex: 1; min-width: 180px; background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 10px 12px; }
.mem-block-title { font-size: 12px; font-weight: 600; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; letter-spacing: .04em; }

/* gauge */
.gauge-wrap { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.gauge-bar { flex: 1; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; }
.gauge-fill { height: 100%; border-radius: 4px; }
.gauge-fill.ok   { background: #3fb950; }
.gauge-fill.warn { background: #d29922; }
.gauge-fill.crit { background: #f85149; }
.gauge-label { font-size: 13px; font-weight: 700; min-width: 46px; text-align: right; }
.gauge-label.ok   { color: #3fb950; }
.gauge-label.warn { color: #d29922; }
.gauge-label.crit { color: #f85149; }

/* badge */
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }
.badge.ok   { background: #1a3027; color: #3fb950; }
.badge.warn { background: #2d2208; color: #d29922; }
.badge.crit { background: #2d0f0f; color: #f85149; }

/* top tables */
.top-table-wrap { margin-bottom: 20px; }
.top-title { font-size: 12px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 8px; }
.top-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.top-table th { text-align: right; padding: 4px 8px; color: #8b949e; font-weight: 500; border-bottom: 1px solid #21262d; }
.top-table th:first-child { text-align: center; }
.top-table td { padding: 3px 8px; border-bottom: 1px solid #161b22; }
.top-table td.num { text-align: right; font-family: monospace; }
.top-table td.dim { color: #8b949e; text-align: center; }
.top-table tbody tr:hover { background: #1c2128; }

.empty { color: #8b949e; font-style: italic; font-size: 13px; padding: 8px 0; }
"""

JS = """
(function () {
  var tabs    = document.querySelectorAll('.tab');
  var pages   = document.querySelectorAll('.page');
  var btn      = document.getElementById('proxy-btn');
  var btnLabel = document.getElementById('proxy-btn-label');
  var panel    = document.getElementById('proxy-panel');
  var search   = document.getElementById('proxy-search');
  var items    = panel ? panel.querySelectorAll('.proxy-item') : [];
  var emptyEl  = panel ? panel.querySelector('.proxy-empty') : null;
  var proxyDefaultLabel = btnLabel ? btnLabel.textContent : '';

  function show(id) {
    tabs.forEach(function(t) { t.classList.toggle('active', t.dataset.page === id); });
    pages.forEach(function(p) { p.classList.toggle('active', p.id === 'page-' + id); });
    if (panel) {
      items.forEach(function(it) { it.classList.toggle('active', it.dataset.page === id); });
      var activeItem = Array.prototype.find ? Array.prototype.find.call(items, function(it) { return it.dataset.page === id; }) : null;
      var inPanel = !!activeItem;
      if (btn) btn.classList.toggle('active', inPanel);
      if (btnLabel) {
        btnLabel.textContent = (inPanel && activeItem)
          ? activeItem.textContent.replace(/\s*⚠$/, '').trim()
          : proxyDefaultLabel;
      }
    }
    try { sessionStorage.setItem('diaginfo-page', id); } catch(e) {}
  }

  function closePanel() {
    if (!panel) return;
    panel.classList.remove('open');
    if (btn) btn.classList.remove('open');
    if (search) { search.value = ''; filterItems(''); }
  }

  function filterItems(q) {
    var visible = 0;
    items.forEach(function(it) {
      var match = !q || it.textContent.toLowerCase().indexOf(q) !== -1;
      it.classList.toggle('hidden', !match);
      if (match) visible++;
    });
    if (emptyEl) emptyEl.style.display = visible === 0 ? 'block' : 'none';
  }

  tabs.forEach(function(tab) {
    tab.addEventListener('click', function() { closePanel(); show(tab.dataset.page); });
  });

  // Перемещаем панель в body чтобы избежать обрезки overflow tab-bar
  if (panel) {
    document.body.appendChild(panel);
    panel.style.position = 'fixed';
  }

  function positionPanel() {
    if (!btn || !panel) return;
    var r = btn.getBoundingClientRect();
    panel.style.top = (r.bottom + 4) + 'px';
    panel.style.left = Math.max(0, r.right - panel.offsetWidth) + 'px';
    panel.style.right = 'auto';
  }

  if (btn && panel) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var opening = !panel.classList.contains('open');
      if (opening) positionPanel();
      panel.classList.toggle('open', opening);
      btn.classList.toggle('open', opening);
      if (opening && search) { search.focus(); }
    });
  }

  if (search) {
    search.addEventListener('input', function() {
      filterItems(this.value.trim().toLowerCase());
    });
    search.addEventListener('search', function() {
      filterItems(this.value.trim().toLowerCase());
    });
    search.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closePanel();
    });
  }

  items.forEach(function(it) {
    it.addEventListener('click', function() {
      show(it.dataset.page);
      closePanel();
    });
  });

  document.addEventListener('click', function(e) {
    if (panel && panel.classList.contains('open')) {
      if (!panel.contains(e.target) && e.target !== btn) closePanel();
    }
  });

  // Восстановить последнюю вкладку или открыть первую
  var saved = null;
  try { saved = sessionStorage.getItem('diaginfo-page'); } catch(e) {}
  var allIds = [];
  tabs.forEach(function(t) { allIds.push(t.dataset.page); });
  items.forEach(function(it) { allIds.push(it.dataset.page); });
  var first = allIds.length ? allIds[0] : null;
  show(saved && document.getElementById('page-' + saved) ? saved : first);
})();
"""

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zabbix diaginfo — {ts}</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <svg class="zabbix-logo" viewBox="0 0 40 40" fill="none">
      <rect width="40" height="40" rx="6" fill="#d40000"/>
      <path d="M8 10h24L20 30 8 10z" fill="white" opacity=".9"/>
    </svg>
    <h1>Zabbix — diaginfo</h1>
    <div class="meta">Сформирован: {ts}</div>
  </header>
  <div class="tab-bar">
    {tabs}
    {proxy_select}
  </div>
  <main>
    {pages}
  </main>
  <script>{js}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("Нет данных на stdin.", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Ошибка разбора JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Нормализуем legacy плоский JSON
    if "server" in data or "proxies" in data:
        pass
    elif "historycache" in data or "valuecache" in data:
        data = {"server": {"name": "Zabbix Server", "diaginfo": data}, "proxies": []}
    else:
        print("Неизвестный формат JSON.", file=sys.stderr)
        sys.exit(1)

    # Список нод: сервер первым, затем прокси
    nodes = []
    if data.get("server"):
        nodes.append(data["server"])
    for proxy in data.get("proxies", []):
        nodes.append(proxy)

    # Разделяем ноды: прокси (из списка proxies) идут в select, остальные — в табы
    proxy_names = {make_page_id(p["name"]) for p in data.get("proxies", [])}

    tabs_html = ""
    proxy_items_html = ""
    has_proxy_error = False
    proxy_count = len(data.get("proxies", []))

    for node in nodes:
        page_id = make_page_id(node["name"])
        is_error = "error" in node and "diaginfo" not in node
        warn = " ⚠" if is_error else ""

        if page_id in proxy_names:
            err_cls = " error" if is_error else ""
            proxy_items_html += f'<div class="proxy-item{err_cls}" data-page="{page_id}">{node["name"]}{warn}</div>\n'
            if is_error:
                has_proxy_error = True
        else:
            err_cls = " error" if is_error else ""
            tabs_html += f'<button class="tab{err_cls}" data-page="{page_id}">{node["name"]}{warn}</button>\n'

    # Кастомный дропдаун для проксей (только если есть хоть один прокси)
    proxy_select_html = ""
    if proxy_items_html:
        btn_cls = " has-error" if has_proxy_error else ""
        proxy_select_html = (
            f'<div class="proxy-dropdown">'
            f'<button class="proxy-btn{btn_cls}" id="proxy-btn">'
            f'<span id="proxy-btn-label">Прокси ({proxy_count})</span>'
            f'<span class="proxy-btn-arrow">▾</span>'
            f'</button>'
            f'<div class="proxy-panel" id="proxy-panel">'
            f'<div class="proxy-search-wrap">'
            f'<input class="proxy-search" id="proxy-search" type="text" placeholder="Поиск…" autocomplete="off">'
            f'</div>'
            f'<div class="proxy-list">'
            f'{proxy_items_html}'
            f'<div class="proxy-empty" style="display:none">Не найдено</div>'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    # Страницы
    pages_html = "".join(render_page(n) for n in nodes)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(HTML_TEMPLATE.format(css=CSS, js=JS, tabs=tabs_html,
                               proxy_select=proxy_select_html, pages=pages_html, ts=ts))


if __name__ == "__main__":
    main()
