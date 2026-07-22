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

# zbx_conf_check

Проверка файла конфигурации Zabbix-сервера (zabbix_server.conf):

- проверка обязательных параметров (список задается константой `REQUIRED_PARAMS` в начале скрипта);
- оценка объема памяти, выделяемой под кэши (сумма всех настраиваемых кэшей с учетом значений по умолчанию и условий их выделения);
- оценка количества соединений с базой данных с детализацией по параметрам, в том числе отсутствующим в конфиге (значения по умолчанию выводятся отдельным блоком).

Значения по умолчанию и поведение процессов взяты из исходного кода Zabbix-сервера (директория `zabbix/`).

## Использование

```
python3 zbx_conf_check.py <путь до zabbix_server.conf>
```

Код возврата: `0` — все обязательные параметры заданы, `1` — есть незаданные параметры или файл не прочитан.

## Примеры

```bash
python3 zbx_conf_check.py /etc/zabbix/zabbix_server.conf
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
