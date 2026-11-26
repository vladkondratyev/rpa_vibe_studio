<div align="center">

# ◆ RPA Vibe Studio

**Отдайте рутину роботам. Людям — работу, где нужна голова.**

Open-source RPA-платформа: соберите процесс из блоков мышкой, а роботы на Windows, Linux и macOS выполнят его по расписанию на ваших серверах — с журналом каждого шага и скриншотом при сбое.

[Сайт](https://rpa.qso-log.ru) · [Скачать](https://github.com/vladkondratyev/rpa_vibe_studio/releases/latest) · [Установка](#-установка-за-5-минут) · [Возможности](#-возможности) · [Roadmap](#-что-дальше)

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?logo=playwright&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

![Дизайнер процессов](docs/img/designer.webp)

</div>

## Зачем

Сотрудники часами переносят данные из личных кабинетов в Excel, каждое утро скачивают выписки, построчно сверяют реестры с порталами. Если действие можно описать инструкцией «открой — найди — скопируй — вставь», его может выполнять робот — через тот же интерфейс, что и человек, без доработки ваших систем.

- **Без программирования** — процесс собирается из 46 готовых блоков в визуальном дизайнере.
- **Без потолка** — нет нужного блока? Блок «Python-код» и любая библиотека Python.
- **Без облака** — оркестратор, роботы и данные остаются в вашем контуре.
- **Без лицензий на робота** — MIT, ставьте сколько угодно роботов.
- **Без Microsoft Office** — Excel-книги читаются и пишутся напрямую, даже на Linux.

## ✨ Возможности

| | |
|---|---|
| 🧩 **Визуальный дизайнер** | Drag-and-drop, условия и циклы, переменные, выражения `{{ row['Сумма'] * 1.2 }}` в любых полях, проверка процесса до запуска, версии, копирование блоков, Ctrl+S |
| 🌐 **Web** (17 блоков) | Клики, ввод, списки, клавиши; селекторы CSS / XPath / `text=` / `role=`; ожидание элементов; извлечение таблиц; скачивание и загрузка файлов; JavaScript; скриншоты. Chromium, Firefox, WebKit, headless |
| 📊 **Excel** (9 блоков) | Книги `.xlsx` без установленного Excel: ячейки, диапазоны, таблицы с заголовками → список строк, добавление строк, листы |
| 📁 **Файлы** (10 блоков) | Поиск по маске, копирование, перемещение, удаление, папки, текст и CSV в любой кодировке |
| 🤖 **Оркестратор** | Очередь задач, роботы с метками (`os=windows`, `excel`, `finance`), назначение на конкретного робота, остановка запуска, обнаружение пропавших роботов |
| ⏰ **Расписания** | cron и пресеты, свои параметры и метки для каждого расписания, время следующего запуска |
| 🔍 **Разбор ошибок** | Журнал по шагам в реальном времени, скриншот в момент сбоя, переход «показать блок в дизайнере», ветка «ошибка» для обработки сбоев внутри процесса |
| 🔌 **REST API** | Запуск процессов, статусы, результаты, журналы и файлы — из 1С, BPM, порталов и скриптов |
| 🍏 **Desktop macOS** | Управление приложениями через AppleScript из Python-блока (пример: `examples/calculator.json` — Калькулятор → Pages) |

<table>
<tr>
<td width="50%"><img src="docs/img/job-failed.webp" alt="Разбор ошибки"><br><sub>Сбой — с текстом ошибки, блоком и скриншотом страницы</sub></td>
<td width="50%"><img src="docs/img/robots.webp" alt="Роботы"><br><sub>Роботы на Windows и Linux, метки, текущие задачи</sub></td>
</tr>
</table>

## 🚀 Установка за 5 минут

Нужен **Python 3.9+**. Готовый архив с собранным веб-интерфейсом — в [Releases](https://github.com/vladkondratyev/rpa_vibe_studio/releases/latest) (Node.js не нужен).

**1. Оркестратор (Linux / macOS)**

```bash
unzip rpa-vibe-studio-0.1.0.zip && cd rpa-vibe-studio-0.1.0
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export RPA_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))"); echo $RPA_TOKEN
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
```

**Windows (PowerShell)**

```powershell
Expand-Archive rpa-vibe-studio-0.1.0.zip -DestinationPath .; cd rpa-vibe-studio-0.1.0
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:RPA_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"; $env:RPA_TOKEN
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
```

Откройте `http://<сервер>:8000` и введите токен.

**2. Робот** — на каждой машине, где он будет работать (тот же архив):

```bash
pip install -r requirements.txt
python -m playwright install chromium          # для Web-блоков
python -m rpa_robot agent --server http://<сервер>:8000 --name robot-01 --tags web,excel --token <RPA_TOKEN>
```

**3. Отладка без оркестратора**

```bash
python -m rpa_robot run examples/calculator.json --var 'expression=12*7'
```

### Настройки

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `RPA_TOKEN` | общий токен для UI, API и роботов | пусто — API открыт (только для разработки!) |
| `RPA_DATABASE_URL` | БД, например `postgresql+psycopg://user:pass@host/rpa` | `sqlite:///./rpa.db` |
| `RPA_SERVER`, `RPA_ROBOT_NAME`, `RPA_ROBOT_TAGS` | значения по умолчанию для робота | |

Для PostgreSQL дополнительно: `pip install "psycopg[binary]"`. Для приложений macOS дайте программе, из которой запущен робот, доступ в «Конфиденциальность → Универсальный доступ».

## 🏗 Архитектура и стек

```
 Браузер ──► Оркестратор (FastAPI) ──► очередь ──► Роботы (Windows / Linux / macOS)
 Дизайнер     процессы · версии ·                   движок графа блоков
 (React)      расписания · журналы ◄── логи, ────   Playwright · openpyxl · Python
              PostgreSQL / SQLite      скриншоты
```

Всё построено на проверенных open-source компонентах:

| Компонент | Роль | Лицензия |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) | HTTP API оркестратора | MIT / BSD-3 |
| [SQLAlchemy 2](https://www.sqlalchemy.org) | хранение процессов, запусков, журналов (SQLite, PostgreSQL) | MIT |
| [Pydantic 2](https://docs.pydantic.dev) | валидация API | MIT |
| [croniter](https://github.com/kiorky/croniter) | расписания cron | MIT |
| [Playwright](https://playwright.dev/python/) | управление браузерами Chromium / Firefox / WebKit | Apache-2.0 |
| [openpyxl](https://openpyxl.readthedocs.io) | чтение и запись `.xlsx` без Excel | MIT |
| [HTTPX](https://www.python-httpx.org) | связь робота с оркестратором | BSD-3 |
| [React 18](https://react.dev) + [React Flow](https://reactflow.dev) | веб-интерфейс и редактор графа | MIT |
| [Vite](https://vitejs.dev) + [TypeScript](https://www.typescriptlang.org) | сборка фронтенда | MIT / Apache-2.0 |

```
frontend/       веб-интерфейс: дизайнер, запуски, роботы, расписания
orchestrator/   HTTP API: процессы, очередь, журналы, cron
rpa_robot/      агент робота (pull-модель: робот сам забирает задачи — не нужен входящий доступ к роботу)
rpa_engine/     движок исполнения графа + блоки Логика / Web / Excel / Файлы
tests/          тесты движка и сквозные тесты API + робот
```

**Новый блок** — одна функция с декоратором `@block(...)` в `rpa_engine/blocks/*.py`; дизайнер подхватит её автоматически через `/api/blocks`.

## 🔭 Что дальше

Архитектура (реестр блоков + граф + pull-роботы) позволяет наращивать платформу без переделки ядра. Направления развития:

- **Vibe-автоматизация** — процесс по текстовому описанию: «каждое утро скачай выписки из банка и разложи по папкам» → готовая схема в дизайнере (LLM).
- **AI-блоки** — разбор счетов, договоров и писем, извлечение полей из PDF и сканов, классификация обращений.
- **Рекордер** — запись действий пользователя в браузере и превращение их в блоки.
- **Desktop-блоки** — Windows UI Automation и macOS Accessibility с селекторами элементов окон.
- **OCR и компьютерное зрение** — работа с интерфейсами, где нет доступных элементов (RDP, Citrix).
- **Почта и мессенджеры** — IMAP/SMTP, Exchange, уведомления в Telegram о сбоях и итогах.
- **Интеграции** — 1С (OData/HTTP-сервисы), REST/SOAP-блоки, базы данных.
- **Хранилище секретов** — пароли порталов отдельно от процессов, интеграция с Vault.
- **Пользователи и роли** — RBAC, SSO / LDAP / Active Directory, журнал действий.
- **Очереди данных** — транзакционная обработка элементов с повторами и SLA.
- **Аналитика и ROI** — сэкономленные часы, успешность процессов, загрузка роботов.
- **Docker / Helm**, Git-версионирование процессов, маркетплейс блоков.

Хотите что-то из этого раньше — создайте issue или пришлите pull request.

## Разработка

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests          # тесты
cd frontend && npm install && npm run dev    # UI на :5173, /api проксируется на :8000
npm run build                                # сборка UI в frontend/dist — его раздаёт оркестратор
```

## Лицензия

[MIT](LICENSE) — используйте, изменяйте и встраивайте свободно, в том числе в коммерческих проектах.
