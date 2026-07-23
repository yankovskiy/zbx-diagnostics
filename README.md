# zbx_loganalyzer

Анализатор лога Zabbix Server. Поддерживает два режима: извлечение блоков профилирования и агрегация времени выполнения LLD-правил.

## Использование

```
python3 zbx_loganalyzer.py --mode <profiling|lld> [options]
```

## Параметры

| Параметр | Описание | По умолчанию |
|---|---|---|
| `--mode` | Режим работы: `profiling` или `lld` | обязательный |
| `--log` | Путь к файлу лога | `/var/log/zabbix/zabbix_server.log` |
| `--pid` | Фильтр по PID процесса | все процессы |
| `--after` | Показывать записи после даты/времени (`YYYY-MM-DD HH:MM:SS`) | — |
| `--before` | Показывать записи до даты/времени (`YYYY-MM-DD HH:MM:SS`) | — |
| `--top N` | (только `lld`) Показать топ N правил по суммарному времени; `0` — все | `10` |

## Режимы

**`profiling`** — извлекает и выводит блоки строк `=== Profiling statistics` из лога.

**`lld`** — вычисляет для каждого LLD-правила количество запусков, суммарное, среднее и максимальное время выполнения. Выводит таблицу, отсортированную по суммарному времени.

## Примеры

```bash
# Блоки профилирования за конкретный час
python3 zbx_loganalyzer.py --mode profiling \
    --after "2026-04-12 10:00:00" --before "2026-04-12 11:00:00"

# Топ-20 медленных LLD-правил
python3 zbx_loganalyzer.py --mode lld --top 20

# LLD по конкретному PID, все правила
python3 zbx_loganalyzer.py --mode lld --pid 12345 --top 0

# Нестандартный путь к логу
python3 zbx_loganalyzer.py --mode profiling --log /opt/zabbix/zabbix_server.log
```

---

# zbx_prof_report

Генерация интерактивного HTML-отчёта по логам runtime-профайлера Zabbix Server.

Профайлер выводит накопительные счётчики по каждому процессу. Скрипт вычисляет дельты между соседними снимками одного PID, агрегирует данные по процессам, функциям, PID и временным интервалам, и формирует самодостаточный HTML-отчёт.

## Содержимое отчёта

- сводные метрики (период, длительность, количество снимков, PID, суммарное время ожидания и обработки);
- топ функций по времени ожидания блокировок и по времени обработки (processing busy);
- таймлайн (stacked bar chart) с разбивкой по типам процессов и настраиваемым размером бакета;
- таблица по типам процессов (waiting, holding, wait ratio, avg wait/lock);
- таблица функций с поиском и фильтрацией по scope и процессу, включая P95 и максимумы;
- сравнение по PID;
- покрытие снимками (snapshot coverage).

## Использование

```
python3 zbx_prof_report.py <путь к логу> [options]
```

## Параметры

| Параметр | Описание | По умолчанию |
|---|---|---|
| `log` | Путь к `zabbix_server.log` или `profiling.log` с блоками профайлера | обязательный |
| `-o, --output` | Путь к выходному HTML-файлу | `zbx-prof-report.html` |
| `--json` | Путь для выгрузки нормализованных данных в JSON | — |
| `--bucket N` | Размер бакета таймлайна в секундах | `60` |
| `--top N` | Количество строк в секциях «Top» | `20` |
| `--p95-samples N` | Размер резервуарной выборки для приближённого P95 (меньше — экономнее по памяти) | `10000` |

## Примеры

```bash
# Отчёт из лога с блоками профайлера
python3 zbx_prof_report.py /var/log/zabbix/zabbix_server.log

# Указать выходной файл и размер бакета 5 минут
python3 zbx_prof_report.py profiling.log -o report.html --bucket 300

# Дополнительно сохранить JSON с данными отчёта
python3 zbx_prof_report.py profiling.log --json report.json
```

---

# zbx_conf_check

Проверка файла конфигурации Zabbix-сервера (zabbix_server.conf):

- проверка обязательных параметров (список задается константами `REQUIRED_PARAMS_V6` / `REQUIRED_PARAMS_V7` в начале скрипта);
- оценка объема памяти, выделяемой под кэши (сумма всех настраиваемых кэшей с учетом значений по умолчанию и условий их выделения);
- оценка количества соединений с базой данных с детализацией по параметрам, в том числе отсутствующим в конфиге (значения по умолчанию выводятся отдельным блоком).

Значения по умолчанию и поведение процессов взяты из исходного кода Zabbix-сервера (директория `zabbix/`).

## Поддерживаемые версии

Скрипт учитывает отличия Zabbix 6 и Zabbix 7: набор известных параметров конфига, значения по умолчанию и список процессов, держащих соединение с БД. Версия выбирается параметром `--zabbix-version`.

Основные отличия Zabbix 7, учтённые скриптом:

- удалены параметры системного аудита (`EnableSystemAudit` и связанные);
- `StartPollers` дополнен новыми типами poller'ов (`StartAgentPollers`, `StartSNMPPollers`, `StartHTTPAgentPollers`, `StartBrowserPollers`);
- новые параметры и процессы (`StartConnectors`, configuration syncer worker, proxy group manager, discovery manager и др.);
- изменённые значения по умолчанию (`StartDiscoverers`, `StartPreprocessors`).

## Использование

```
python3 zbx_conf_check.py <путь до zabbix_server.conf> [--zabbix-version {6,7}]
```

| Параметр | Описание | По умолчанию |
|---|---|---|
| `--zabbix-version` | Версия Zabbix, для которой проверяется конфиг (`6` или `7`) | `6` |

Код возврата: `0` — все обязательные параметры заданы, `1` — есть незаданные параметры или файл не прочитан.

## Примеры

```bash
# Проверка конфига Zabbix 6 (поведение по умолчанию)
python3 zbx_conf_check.py /etc/zabbix/zabbix_server.conf

# Проверка конфига Zabbix 7
python3 zbx_conf_check.py --zabbix-version 7 /etc/zabbix/zabbix_server.conf
```

---

# zbx_diaginfo

Инструмент для сбора диагностической информации (`diaginfo`) с Zabbix Server и всех подключённых прокси через Task Manager API, с последующей визуализацией в HTML-отчёт.

## Поддерживаемые версии Zabbix

| Версия | Сборка |
|--------|--------|
| Zabbix 6.x | Официальная сборка от вендора |
| Zabbix 7.x | [Extended Edition](https://github.com/yankovskiy/zabbix) 1.5.0+ |

Версия определяется автоматически при подключении. Для каждой версии используется соответствующий набор секций и параметров API.

## Требования

- Python 3.8+
- Права пользователя Zabbix: роль **Super Admin** (или доступ к `task.create` / `task.get`)

```bash
pip install -r requirements.txt
```

## Использование

`zbx_diaginfo.py` собирает данные с сервера и всех прокси, выводит JSON в stdout. `zbx_diaginfo_html.py` читает этот JSON из stdin и генерирует HTML-отчёт.

### Базовый сценарий

```bash
python3 zbx_diaginfo.py --url http://zabbix.example.com --user Admin --password secret \
  | python3 zbx_diaginfo_html.py > report.html
```

### Только сервер, без прокси

```bash
python3 zbx_diaginfo.py --url http://zabbix.example.com --no-proxies \
  | python3 zbx_diaginfo_html.py > report.html
```

### Только отдельные прокси

```bash
python3 zbx_diaginfo.py --url http://zabbix.example.com \
  --proxy moscow --proxy spb \
  | python3 zbx_diaginfo_html.py > report.html
```

### Сохранить сырой JSON для последующей обработки

```bash
python3 zbx_diaginfo.py --url http://zabbix.example.com > data.json
python3 zbx_diaginfo_html.py < data.json > report.html
```

### С mTLS

```bash
python3 zbx_diaginfo.py --url https://zabbix.example.com \
  --mtls --cert client.crt --key client.key --ca ca.crt \
  | python3 zbx_diaginfo_html.py > report.html
```

## Ключи zbx_diaginfo.py

### Подключение

| Ключ | По умолчанию | Описание |
|------|-------------|----------|
| `--url URL` | `http://localhost/zabbix` | URL Zabbix frontend |
| `--user USER` | `Admin` | Имя пользователя Zabbix API |
| `--password PASSWORD` | `zabbix` | Пароль пользователя |

### Фильтрация прокси

| Ключ | Описание |
|------|----------|
| `--no-proxies` | Собрать данные только с сервера, прокси игнорируются |
| `--proxy NAME` | Собрать данные только с указанного прокси. Можно указать несколько раз. Поиск без учёта регистра, поддерживается частичное совпадение имени |

### mTLS

| Ключ | Описание |
|------|----------|
| `--mtls` | Включить взаимную TLS-аутентификацию |
| `--cert PATH` | Путь к клиентскому сертификату (PEM). Обязателен при `--mtls` |
| `--key PATH` | Путь к приватному ключу клиента (PEM). Обязателен при `--mtls` |
| `--ca PATH` | Путь к CA-сертификату для проверки сервера (PEM). Если не указан, используется системное хранилище |

## Что собирается

**С сервера:**

| Секция | Zabbix 6 | Zabbix 7 EE |
|--------|----------|-------------|
| `historycache` | топ по значениям | топ по значениям |
| `valuecache` | топ по значениям и запросам | топ по значениям и запросам |
| `preprocessing` | статистика очереди | статистика очереди + топ по peak, sequences, values_num, values_sz, time_ms, total_ms |
| `alerting` | топ по типам медиа и источникам | топ по типам медиа и источникам |
| `lld` | топ правил по значениям | топ правил по значениям |
| `connector` | — | топ коннекторов по значениям |

**С прокси** (ограничение API Zabbix):

| Секция | Zabbix 6 | Zabbix 7 EE |
|--------|----------|-------------|
| `historycache` | топ по значениям | топ по значениям |
| `preprocessing` | статистика очереди | статистика очереди + топ по peak, sequences, values_num, values_sz, time_ms, total_ms |

## Формат вывода zbx_diaginfo.py

```json
{
  "server": {
    "name": "Zabbix Server",
    "diaginfo": { ... }
  },
  "proxies": [
    {
      "proxyid": "12345",
      "name": "proxy-moscow",
      "diaginfo": { ... }
    }
  ]
}
```

При ошибке сбора данных с узла вместо `diaginfo` будет поле `error`.

## Примеры

```bash
# Локальная генерация отчёта из примера JSON (zabbix7.json входит в репозиторий)
python3 zbx_diaginfo_html.py < sample/zabbix7.json > report.html
```
