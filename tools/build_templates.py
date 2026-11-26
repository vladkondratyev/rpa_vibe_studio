"""Генератор галереи шаблонов процессов (templates/*/*.json).

    python tools/build_templates.py

Каждый шаблон — готовый граф блоков с понятными подписями и заглушками
параметров; после создания процесса из шаблона остаётся подставить свои
адреса, пути, селекторы и имена секретов.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rpa_engine import validate  # noqa: E402


class Flow:
    """Построитель графа: цепочка блоков с ветками."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = [{"id": "start", "type": "core.start", "params": {}}]
        self.edges: List[Dict[str, Any]] = []
        self.pos: Dict[str, Tuple[int, int]] = {"start": (0, 0)}

    def node(self, nid: str, type_: str, label: str, col: int, row: int, **params: Any) -> str:
        self.nodes.append({"id": nid, "type": type_, "label": label, "params": params})
        self.pos[nid] = (col, row)
        return nid

    def link(self, src: str, dst: str, handle: str = "out") -> None:
        self.edges.append({"id": f"e{len(self.edges)}", "source": src, "sourceHandle": handle, "target": dst})

    def chain(self, *ids: str) -> None:
        for a, b in zip(ids, ids[1:]):
            self.link(a, b)

    def build(self, variables: List[Dict[str, Any]], outputs: List[str]) -> Dict[str, Any]:
        for n in self.nodes:
            c, r = self.pos[n["id"]]
            n["position"] = {"x": 40 + c * 330, "y": 40 + r * 170}
        return {"variables": variables, "outputs": outputs, "nodes": self.nodes, "edges": self.edges}


def end(f: Flow, col: int, row: int, nid: str = "end") -> str:
    return f.node(nid, "core.end", "Готово", col, row)


TEMPLATES: List[Dict[str, Any]] = []


def template(group: str, slug: str, name: str, category: str, description: str, minutes: int,
             tags: List[str], requires: Optional[List[str]] = None):
    def deco(fn):
        TEMPLATES.append({"group": group, "slug": slug, "name": name, "category": category,
                          "description": description, "minutes_saved": minutes, "tags": tags,
                          "requires": requires or [], "definition": fn()})
        return fn
    return deco


# ================================================================ БАНК
@template("bank", "cbr_rates_daily", "Курсы валют ЦБ РФ в Excel и Telegram", "Банк",
          "Каждое утро загружает официальные курсы Банка России, дописывает строку в журнал курсов "
          "Excel и отправляет сводку USD/EUR/CNY в чат казначейства.", 10, ["ЦБ РФ", "казначейство", "Excel"])
def _():
    f = Flow()
    f.node("rates", "bank.cbr_rates", "Курсы ЦБ на сегодня", 1, 0, result="rates")
    f.node("book", "excel.open", "Журнал курсов", 2, 0, path="{{ журнал }}", book="курсы", create=True)
    f.node("row", "excel.append_row", "Добавить строку", 3, 0, book="курсы", sheet="Курсы",
           values="[rates['date'], rates['rates']['USD']['per_unit'], rates['rates']['EUR']['per_unit'], "
                  "rates['rates']['CNY']['per_unit']]")
    f.node("save", "excel.save", "Сохранить", 4, 0, book="курсы")
    f.node("msg", "notify.telegram", "Сводка в Telegram", 1, 1, token="telegram_bot", chat_id="{{ чат }}",
           text="Курсы ЦБ на {{ rates['date'] }}:\nUSD {{ rates['rates']['USD']['per_unit'] }}\n"
                "EUR {{ rates['rates']['EUR']['per_unit'] }}\nCNY {{ rates['rates']['CNY']['per_unit'] }}")
    end(f, 2, 1)
    f.chain("start", "rates", "book", "row", "save", "msg", "end")
    return f.build([{"name": "журнал", "default": "D:\\Казначейство\\курсы_цб.xlsx"},
                    {"name": "чат", "default": "-1001234567890"}], ["rates"])


@template("bank", "key_rate_monitor", "Мониторинг ключевой ставки ЦБ", "Банк",
          "Проверяет ключевую ставку Банка России и при изменении сообщает в Яндекс Мессенджер и "
          "создаёт задачу в Трекере на пересмотр ставок по продуктам.", 15, ["ЦБ РФ", "ALM", "Яндекс"])
def _():
    f = Flow()
    f.node("kr", "bank.cbr_key_rate", "Ключевая ставка за 30 дней", 1, 0, days=30, result="ставки")
    f.node("calc", "core.python", "Сравнить с прошлым значением", 2, 0,
           code="текущая = ставки[-1]['rate'] if ставки else None\n"
                "прошлая = next((s['rate'] for s in reversed(ставки) if s['rate'] != текущая), текущая)\n"
                "изменилась = текущая != прошлая and ставки[-1]['date'] >= str(today() - datetime.timedelta(days=1))")
    f.node("if", "core.if", "Изменилась?", 3, 0, condition="изменилась")
    f.node("msg", "yandex.messenger", "Сообщение в Мессенджер", 4, 0, token="yandex_bot", chat_id="{{ чат }}",
           text="Ключевая ставка изменена: {{ прошлая }}% → {{ текущая }}%")
    f.node("task", "yandex.tracker_issue", "Задача в Трекер", 5, 0, token="yandex_oauth", org_id="{{ организация }}",
           queue="ALM", summary="Пересмотреть ставки: ключевая {{ текущая }}%",
           description="Банк России изменил ключевую ставку с {{ прошлая }}% до {{ текущая }}%.")
    f.node("same", "core.log", "Без изменений", 3, 1, message="Ставка без изменений: {{ текущая }}%")
    end(f, 6, 0)
    f.chain("start", "kr", "calc", "if")
    f.link("if", "msg", "true")
    f.chain("msg", "task", "end")
    f.link("if", "same", "false")
    f.link("same", "end")
    return f.build([{"name": "чат", "default": "0/0/xxxxxxxx"}, {"name": "организация", "default": "bpf..."}],
                   ["текущая"])


@template("bank", "statement_1c_registry", "Выписка 1С → реестр операций Excel", "Банк",
          "Разбирает выписку в формате 1CClientBankExchange (kl_to_1c.txt), выгружает операции в Excel и "
          "считает обороты по контрагентам.", 30, ["1С", "выписка", "Excel"])
def _():
    f = Flow()
    f.node("read", "bank.read_1c", "Прочитать выписку", 1, 0, path="{{ файл_выписки }}", result="выписка")
    f.node("ops", "core.python", "Операции в таблицу", 2, 0,
           code="операции = [{'Дата': d.get('Дата'), 'Номер': d.get('Номер'), 'Сумма': d.get('Сумма'),\n"
                "             'Плательщик': d.get('Плательщик'), 'Получатель': d.get('Получатель'),\n"
                "             'ИНН получателя': d.get('ПолучательИНН'), 'Назначение': d.get('НазначениеПлатежа')}\n"
                "            for d in выписка['documents']]")
    f.node("grp", "table.group_sum", "Обороты по получателям", 3, 0, rows="операции", by="Получатель", sum="Сумма",
           result="обороты")
    f.node("book", "excel.open", "Новая книга", 4, 0, path="{{ отчёт }}", book="реестр", create=True)
    f.node("t1", "excel.write_table", "Лист «Операции»", 1, 1, book="реестр", sheet="Операции", rows="операции")
    f.node("t2", "excel.write_table", "Лист «Обороты»", 2, 1, book="реестр", sheet="Обороты", rows="обороты")
    f.node("save", "excel.save", "Сохранить", 3, 1, book="реестр")
    end(f, 4, 1)
    f.chain("start", "read", "ops", "grp", "book", "t1", "t2", "save", "end")
    return f.build([{"name": "файл_выписки", "default": "D:\\Обмен\\kl_to_1c.txt"},
                    {"name": "отчёт", "default": "D:\\Отчёты\\реестр_операций.xlsx"}], ["обороты"])


@template("bank", "payments_reconciliation", "Сверка платежей: выписка банка ↔ реестр 1С", "Банк",
          "Сверяет операции из выписки клиент-банка с реестром оплат из 1С по номеру документа и сумме. "
          "Расхождения — в Excel и письмом бухгалтеру.", 90, ["сверка", "1С", "почта"])
def _():
    f = Flow()
    f.node("st", "bank.read_1c", "Выписка банка", 1, 0, path="{{ выписка }}", result="выписка")
    f.node("reg", "onec.query", "Реестр оплат из 1С", 2, 0, base_url="{{ odata_1c }}", username="robot",
           password="onec_password", entity="Document_ПлатежноеПоручениеИсходящее",
           filter="Date ge datetime'{{ (today() - datetime.timedelta(days=1)).isoformat() }}T00:00:00'",
           select="Number,Date,СуммаДокумента", result="реестр")
    f.node("cmp", "table.compare", "Сверить", 3, 0, left="выписка['documents']", right="реестр",
           key="Номер|Number", value="Сумма|СуммаДокумента", tolerance=0.01, result="сверка")
    f.node("if", "core.if", "Есть расхождения?", 4, 0,
           condition="сверка['mismatched'] or сверка['only_left'] or сверка['only_right']")
    f.node("book", "excel.open", "Отчёт о расхождениях", 1, 1, path="{{ отчёт }}", book="сверка", create=True)
    f.node("w1", "excel.write_table", "Расхождения сумм", 2, 1, book="сверка", sheet="Суммы",
           rows="[{'Номер': m['key'], 'Банк': m['left']['Сумма'], '1С': m['right']['СуммаДокумента'], "
                "'Разница': m['difference']} for m in сверка['mismatched']]")
    f.node("w2", "excel.write_table", "Нет в 1С", 3, 1, book="сверка", sheet="Нет в 1С", rows="сверка['only_left']")
    f.node("save", "excel.save", "Сохранить", 4, 1, book="сверка")
    f.node("mail", "email.send", "Письмо бухгалтеру", 5, 1, host="smtp.yandex.ru", port=465, security="ssl",
           username="robot@bank.ru", password="smtp_password", sender="robot@bank.ru", to="{{ бухгалтер }}",
           subject="Расхождения сверки за {{ today().strftime('%d.%m.%Y') }}",
           body="Расхождений сумм: {{ len(сверка['mismatched']) }}, нет в 1С: {{ len(сверка['only_left']) }}, "
                "нет в банке: {{ len(сверка['only_right']) }}. Отчёт во вложении.",
           attachments="{{ отчёт }}")
    f.node("ok", "core.log", "Всё сошлось", 4, 2, message="Сверка без расхождений: {{ len(сверка['matched']) }} документов")
    end(f, 6, 1)
    f.chain("start", "st", "reg", "cmp", "if")
    f.link("if", "book", "true")
    f.chain("book", "w1", "w2", "save", "mail", "end")
    f.link("if", "ok", "false")
    f.link("ok", "end")
    return f.build([{"name": "выписка", "default": "D:\\Обмен\\kl_to_1c.txt"},
                    {"name": "odata_1c", "default": "http://1c-srv/buh/odata/standard.odata"},
                    {"name": "отчёт", "default": "D:\\Сверки\\сверка.xlsx"},
                    {"name": "бухгалтер", "default": "buh@bank.ru"}], ["сверка"])


@template("bank", "counterparty_egrul_check", "Проверка контрагентов по ЕГРЮЛ (KYC)", "Банк",
          "Берёт список ИНН из Excel, проверяет контрольные суммы, получает сведения ЕГРЮЛ/ЕГРИП и отмечает "
          "ликвидированные и несуществующие компании.", 120, ["KYC", "ФНС", "контрагенты"])
def _():
    f = Flow()
    f.node("book", "excel.open", "Список контрагентов", 1, 0, path="{{ файл }}", book="контрагенты")
    f.node("rows", "excel.read_table", "Прочитать таблицу", 2, 0, book="контрагенты", sheet="Лист1", result="список")
    f.node("loop", "core.for_each", "По каждому ИНН", 3, 0, collection="список", item="к")
    f.node("inn", "bank.validate", "ИНН корректен?", 3, 1, kind="inn", value="{{ к['ИНН'] }}")
    f.node("egrul", "bank.egrul", "Запрос в ЕГРЮЛ", 4, 1, query="{{ к['ИНН'] }}", result="сведения")
    f.node("delay", "core.delay", "Пауза (лимиты ФНС)", 5, 1, seconds=2)
    f.node("okrow", "excel.append_row", "Результат", 6, 1, book="контрагенты", sheet="Проверка",
           values="[к['ИНН'], сведения[0]['name'], сведения[0]['ogrn'], сведения[0]['director'], "
                  "'ПРЕКРАЩЕНА ' + сведения[0]['terminated'] if сведения[0]['terminated'] else 'действует']")
    f.node("nf", "excel.append_row", "Не найден", 4, 2, book="контрагенты", sheet="Проверка",
           values="[к['ИНН'], '', '', '', 'не найден в ЕГРЮЛ']")
    f.node("bad", "excel.append_row", "Ошибка в ИНН", 3, 2, book="контрагенты", sheet="Проверка",
           values="[к['ИНН'], '', '', '', 'неверная контрольная сумма ИНН']")
    f.node("save", "excel.save", "Сохранить", 4, 0, book="контрагенты")
    end(f, 5, 0)
    f.chain("start", "book", "rows", "loop")
    f.link("loop", "inn", "body")
    f.link("inn", "egrul", "valid")
    f.link("inn", "bad", "invalid")
    f.link("egrul", "delay", "found")
    f.link("egrul", "nf", "not_found")
    f.link("delay", "okrow")
    f.link("loop", "save", "done")
    f.link("save", "end")
    return f.build([{"name": "файл", "default": "D:\\KYC\\контрагенты.xlsx"}], [])


@template("bank", "aml_screening_115fz", "Скрининг клиентов по перечням (115-ФЗ)", "Банк",
          "Ежедневно сверяет клиентов из базы с перечнем Росфинмониторинга и стоп-листами банка: точное "
          "совпадение по ИНН и нечёткое по ФИО. Совпадения — в очередь комплаенс-контроля и задачу в Трекер.",
          180, ["AML", "115-ФЗ", "комплаенс", "очереди"])
def _():
    f = Flow()
    f.node("cl", "db.query", "Клиенты из АБС", 1, 0, connection="postgresql+psycopg://rpa:{password}@abs-db/abs",
           password="abs_db_password", sql="SELECT client_id, full_name AS name, inn FROM clients WHERE active",
           result="клиенты")
    f.node("lst", "files.read_csv", "Перечень (CSV)", 2, 0, path="{{ перечень }}", delimiter=";", result="перечень")
    f.node("scr", "bank.screen", "Сверка с перечнем", 3, 0, subjects="клиенты", watchlist="перечень",
           list_name_field="ФИО", list_inn_field="ИНН", threshold=0.9, result="совпадения")
    f.node("q", "queue.add", "В очередь комплаенса", 4, 0, queue="Комплаенс: совпадения", many=True,
           data="{{ совпадения }}", priority=10)
    f.node("task", "yandex.tracker_issue", "Задача контролёру", 5, 0, token="yandex_oauth",
           org_id="{{ организация }}", queue="AML", summary="Совпадения с перечнем: {{ len(совпадения) }}",
           description="Проверьте элементы очереди «Комплаенс: совпадения».")
    f.node("clean", "core.log", "Совпадений нет", 4, 1, message="Проверено клиентов: {{ len(клиенты) }}, совпадений нет")
    end(f, 6, 0)
    f.chain("start", "cl", "lst", "scr")
    f.link("scr", "q", "hits")
    f.chain("q", "task", "end")
    f.link("scr", "clean", "clean")
    f.link("clean", "end")
    return f.build([{"name": "перечень", "default": "D:\\AML\\перечень_рфм.csv"},
                    {"name": "организация", "default": "bpf..."}], ["совпадения"])


@template("bank", "passport_onboarding", "Открытие счёта: распознавание паспорта", "Банк",
          "Забирает сканы паспортов из почтового ящика отделений, распознаёт поля по шаблону «Паспорт РФ» "
          "(с проверкой MRZ), проверяет ИНН и создаёт анкету клиента через API. Сомнительные случаи — в очередь "
          "ручной проверки.", 12, ["OCR", "паспорт", "онбординг", "очереди"], ["Tesseract или Yandex Vision"])
def _():
    f = Flow()
    f.node("mail", "email.read", "Письма с паспортами", 1, 0, host="imap.yandex.ru", username="onboarding@bank.ru",
           password="imap_password", criteria='UNSEEN SUBJECT "паспорт"', attachments_dir="D:\\Онбординг\\входящие",
           result="письма")
    f.node("files", "core.python", "Список вложений", 2, 0,
           code="сканы = [a['path'] for m in письма for a in m['attachments'] "
                "if a['name'].lower().endswith(('.jpg', '.jpeg', '.png', '.pdf'))]")
    f.node("loop", "core.for_each", "По каждому скану", 3, 0, collection="сканы", item="скан")
    f.node("ocr", "ocr.extract", "Паспорт РФ", 3, 1, file="{{ скан }}", template="passport_rf", engine="yandex",
           yandex="yandex", result="паспорт")
    f.node("api", "http.request", "Создать анкету", 4, 1, method="POST", url="{{ crm }}/api/clients",
           headers="{\"Authorization\": \"Bearer {{ secret('crm_token') }}\"}", body_type="json",
           body="{{ паспорт['fields'] }}", result="анкета")
    f.node("review", "queue.add", "На ручную проверку", 4, 2, queue="Проверка документов",
           data="{{ {'файл': скан, 'распознано': паспорт['fields'], 'нет': паспорт['missing'], "
                "'ошибки': паспорт['errors']} }}", reference="{{ скан }}")
    end(f, 4, 0)
    f.chain("start", "mail", "files", "loop")
    f.link("loop", "ocr", "body")
    f.link("ocr", "api", "ok")
    f.link("ocr", "review", "review")
    f.link("api", "review", "error")
    f.link("loop", "end", "done")
    return f.build([{"name": "crm", "default": "https://crm.bank.local"}], [])


@template("bank", "fns_decisions_queue", "Решения ФНС о приостановлении операций", "Банк",
          "Читает входящие решения и запросы ФНС/ФССП (XML во вложениях), извлекает ИНН, номер и дату решения "
          "и ставит в очередь на исполнение с наивысшим приоритетом и SLA.", 25, ["ФНС", "311-П", "очереди"])
def _():
    f = Flow()
    f.node("mail", "email.read", "Входящие от ФНС", 1, 0, host="imap.bank.local", port=993,
           username="fns@bank.ru", password="imap_password", criteria='UNSEEN FROM "nalog"',
           attachments_dir="D:\\ФНС\\входящие", move_to="Обработано", result="письма")
    f.node("files", "core.python", "XML-вложения", 2, 0,
           code="файлы = [a['path'] for m in письма for a in m['attachments'] if a['name'].lower().endswith('.xml')]")
    f.node("loop", "core.for_each", "По каждому файлу", 3, 0, collection="файлы", item="файл")
    f.node("xml", "data.parse_xml", "Разобрать XML", 3, 1, source="{{ файл }}", result="док")
    f.node("map", "core.python", "Ключевые реквизиты", 4, 1,
           code="import re\nтекст = json.dumps(док, ensure_ascii=False)\n"
                "def найти(шаблон):\n    m = re.search(шаблон, текст)\n    return m.group(1) if m else None\n"
                "решение = {'файл': файл, 'ИНН': найти(r'\"@ИННЮЛ\": \"(\\d{10})\"') or найти(r'\"@ИННФЛ\": \"(\\d{12})\"'),\n"
                "           'номер': найти(r'\"@НомРеш\\w*\": \"([^\"]+)\"'), 'дата': найти(r'\"@ДатаРеш\\w*\": \"([^\"]+)\"')}")
    f.node("q", "queue.add", "В очередь исполнения", 5, 1, queue="Решения ФНС", data="{{ решение }}",
           reference="{{ решение['номер'] }}", priority=100)
    end(f, 4, 0)
    f.chain("start", "mail", "files", "loop")
    f.link("loop", "xml", "body")
    f.chain("xml", "map", "q")
    f.link("loop", "end", "done")
    return f.build([], [])


@template("bank", "abs_citrix_payments", "Ввод платёжек в АБС через Citrix", "Банк",
          "Робот-исполнитель очереди: берёт платёж, в сессии Citrix открывает форму АБС, находит поля по "
          "надписям (OCR) и образцам кнопок, вводит реквизиты, сохраняет скриншот и закрывает элемент. "
          "При сбое элемент вернётся в очередь.", 6, ["Citrix", "АБС", "Vision", "очереди"],
          ["Tesseract", "pyautogui", "OpenCV"])
def _():
    f = Flow()
    f.node("act", "desktop.activate", "Окно Citrix", 1, 0, window="Citrix Workspace|АБС")
    f.node("next", "queue.next", "Следующий платёж", 2, 0, queue="Платежи в АБС", item="item")
    f.node("new", "vision.click_image", "Кнопка «Новый документ»", 3, 0, image="образцы/новый_документ.png",
           confidence=0.85, timeout=20)
    f.node("w1", "vision.wait_text", "Форма открылась", 4, 0, text="Платёжное поручение", timeout=30)
    f.node("acc", "vision.click_text", "Поле «Счёт получателя»", 5, 0, text="Счёт получателя", offset="220,0")
    f.node("t1", "vision.type", "Ввести счёт", 5, 1, text="{{ item['data']['счёт'] }}", hotkey="tab")
    f.node("bik", "vision.click_text", "Поле «БИК»", 4, 1, text="БИК", offset="160,0")
    f.node("t2", "vision.type", "Ввести БИК", 3, 1, text="{{ item['data']['бик'] }}", hotkey="tab")
    f.node("sum", "vision.click_text", "Поле «Сумма»", 2, 1, text="Сумма", offset="160,0")
    f.node("t3", "vision.type", "Ввести сумму", 1, 1, text="{{ '%.2f' % item['data']['сумма'] }}", hotkey="tab")
    f.node("pur", "vision.type", "Назначение платежа", 1, 2, text="{{ item['data']['назначение'] }}")
    f.node("save", "vision.type", "Сохранить (F2)", 2, 2, hotkey="f2")
    f.node("w2", "vision.wait_text", "Документ сохранён", 3, 2, text="сохранен", timeout=20)
    f.node("shot", "vision.screenshot", "Скриншот", 4, 2, name="платёж_{{ item['id'] }}.png")
    f.node("done", "queue.complete", "Платёж введён", 5, 2, item="item")
    f.node("fail", "queue.fail", "Ошибка ввода", 3, 3, item="item", error="{{ last_error }}")
    f.node("esc", "vision.type", "Закрыть форму", 4, 3, hotkey="esc")
    end(f, 0, 1)
    f.chain("start", "act", "next")
    f.link("next", "new", "item")
    f.link("next", "end", "empty")
    f.chain("new", "w1", "acc", "t1", "bik", "t2", "sum", "t3", "pur", "save", "w2", "shot", "done")
    f.link("w2", "fail", "error")
    f.link("new", "fail", "error")
    f.link("w1", "fail", "not_found")
    f.link("fail", "esc")
    f.link("esc", "next")
    f.link("done", "next")
    return f.build([], [])


@template("bank", "loan_applications_ai", "Кредитные заявки: извлечение данных ИИ", "Банк",
          "Разбирает PDF-анкеты заёмщиков из папки: ИИ извлекает ФИО, ИНН, доход, сумму и срок, робот "
          "проверяет ИНН, записывает заявку в базу скоринга и уведомляет кредитный отдел.", 20,
          ["ИИ", "кредиты", "PDF", "SQL"], ["Подключение в «Модели ИИ»"])
def _():
    f = Flow()
    f.node("ls", "files.list", "Новые анкеты", 1, 0, folder="{{ папка }}", pattern="*.pdf", result="анкеты")
    f.node("loop", "core.for_each", "По каждой анкете", 2, 0, collection="анкеты", item="анкета")
    f.node("txt", "docs.pdf_text", "Текст PDF", 2, 1, path="{{ анкета }}", result="текст")
    f.node("ai", "ai.extract", "Извлечь поля (ИИ)", 3, 1, text="{{ текст }}", files="",
           fields="фио: ФИО заёмщика\nинн: ИНН заёмщика\nдоход: ежемесячный доход, руб\n"
                  "сумма: запрошенная сумма кредита, руб\nсрок: срок кредита, месяцев\nтелефон: контактный телефон",
           result="заявка")
    f.node("inn", "bank.validate", "ИНН корректен?", 4, 1, kind="inn", value="{{ заявка['инн'] }}")
    f.node("db", "db.execute", "В базу скоринга", 5, 1, connection="postgresql+psycopg://rpa:{password}@scoring/db",
           password="scoring_db_password",
           sql="INSERT INTO applications (fio, inn, income, amount, term, phone, source) "
               "VALUES (:фио, :инн, :доход, :сумма, :срок, :телефон, 'rpa')", params="{{ заявка }}")
    f.node("mv", "files.move", "В архив", 6, 1, source="{{ анкета }}", target="{{ папка }}\\обработано")
    f.node("bad", "queue.add", "На проверку менеджеру", 4, 2, queue="Заявки: ручная проверка",
           data="{{ dict(заявка, файл=анкета) }}", reference="{{ анкета }}")
    f.node("note", "notify.telegram", "Сводка кредитному отделу", 3, 0, token="telegram_bot", chat_id="{{ чат }}",
           text="Обработано анкет: {{ len(анкеты) }}")
    end(f, 4, 0)
    f.chain("start", "ls", "loop")
    f.link("loop", "txt", "body")
    f.chain("txt", "ai", "inn")
    f.link("inn", "db", "valid")
    f.link("inn", "bad", "invalid")
    f.link("db", "mv")
    f.link("loop", "note", "done")
    f.link("note", "end")
    return f.build([{"name": "папка", "default": "D:\\Кредиты\\анкеты"}, {"name": "чат", "default": "-100123"}], [])


@template("bank", "client_requests_triage", "Обращения клиентов: классификация и задачи", "Банк",
          "Читает почту контакт-центра, ИИ определяет тему обращения (YandexGPT/GigaChat/Claude), робот создаёт "
          "задачу в Яндекс Трекере нужной очереди и отправляет клиенту подтверждение с номером.", 8,
          ["ИИ", "контакт-центр", "Яндекс Трекер", "почта"], ["Подключение в «Модели ИИ»"])
def _():
    f = Flow()
    f.node("mail", "email.read", "Новые обращения", 1, 0, host="imap.yandex.ru", username="support@bank.ru",
           password="imap_password", criteria="UNSEEN", limit=50, result="письма")
    f.node("loop", "core.for_each", "По каждому письму", 2, 0, collection="письма", item="письмо")
    f.node("cls", "ai.classify", "Тема обращения", 2, 1, text="{{ письмо['subject'] }}\n{{ письмо['text'] }}",
           categories="блокировка карты\nоспаривание операции\nзапрос выписки или справки\nкредиты и ипотека\n"
                      "жалоба на обслуживание\nдругое", result="тема")
    f.node("map", "core.set_variable", "Очередь Трекера", 3, 1, name="очередь",
           value="{{ {'блокировка карты': 'CARDS', 'оспаривание операции': 'DISPUTE', "
                 "'кредиты и ипотека': 'LOANS'}.get(тема, 'SUPPORT') }}")
    f.node("task", "yandex.tracker_issue", "Задача в Трекер", 4, 1, token="yandex_oauth", org_id="{{ организация }}",
           queue="{{ очередь }}", summary="[{{ тема }}] {{ письмо['subject'] }}",
           description="От: {{ письмо['from'] }}\n\n{{ письмо['text'][:5000] }}", result="задача")
    f.node("reply", "email.send", "Подтверждение клиенту", 5, 1, host="smtp.yandex.ru", port=465, security="ssl",
           username="support@bank.ru", password="smtp_password", sender="support@bank.ru", to="{{ письмо['from'] }}",
           subject="Обращение {{ задача }} зарегистрировано",
           body="Здравствуйте!\n\nВаше обращение зарегистрировано под номером {{ задача }} "
                "(тема: {{ тема }}). Мы ответим в течение одного рабочего дня.\n\nБанк")
    end(f, 3, 0)
    f.chain("start", "mail", "loop")
    f.link("loop", "cls", "body")
    f.chain("cls", "map", "task", "reply")
    f.link("loop", "end", "done")
    return f.build([{"name": "организация", "default": "bpf..."}], [])


@template("bank", "overdue_notifications", "Уведомления о просроченной задолженности", "Банк",
          "Выбирает из АБС клиентов с просрочкой, формирует персональные письма по шаблону с суммой "
          "прописью и ведёт журнал отправки в Excel.", 60, ["взыскание", "SQL", "почта"])
def _():
    f = Flow()
    f.node("q", "db.query", "Просрочка из АБС", 1, 0, connection="mssql+pyodbc://rpa:{password}@abs/ABS?driver=ODBC+Driver+18+for+SQL+Server",
           password="abs_db_password",
           sql="SELECT contract, client_name, email, overdue_amount, overdue_days FROM v_overdue "
               "WHERE overdue_days BETWEEN :d1 AND :d2 AND email IS NOT NULL",
           params="{\"d1\": 5, \"d2\": 30}", result="должники")
    f.node("book", "excel.open", "Журнал", 2, 0, path="{{ журнал }}", book="журнал", create=True)
    f.node("loop", "core.for_each", "По каждому клиенту", 3, 0, collection="должники", item="д")
    f.node("words", "bank.amount_words", "Сумма прописью", 3, 1, amount="{{ д['overdue_amount'] }}", result="прописью")
    f.node("text", "text.template", "Текст письма", 4, 1, result="письмо",
           template="Уважаемый(ая) {{ д['client_name'] }}!\n\nПо договору {{ д['contract'] }} образовалась "
                    "просроченная задолженность {{ '%.2f' % д['overdue_amount'] }} руб. ({{ прописью }}), "
                    "срок просрочки {{ д['overdue_days'] }} дн.\nПросим погасить задолженность в ближайшее время.")
    f.node("send", "email.send", "Отправить", 5, 1, host="smtp.bank.local", port=587, security="starttls",
           username="collect@bank.ru", password="smtp_password", sender="collect@bank.ru", to="{{ д['email'] }}",
           subject="Просроченная задолженность по договору {{ д['contract'] }}", body="{{ письмо }}")
    f.node("log", "excel.append_row", "Отметка в журнале", 6, 1, book="журнал", sheet="Отправлено",
           values="[str(now())[:16], д['contract'], д['email'], д['overdue_amount']]")
    f.node("save", "excel.save", "Сохранить журнал", 4, 0, book="журнал")
    end(f, 5, 0)
    f.chain("start", "q", "book", "loop")
    f.link("loop", "words", "body")
    f.chain("words", "text", "send", "log")
    f.link("loop", "save", "done")
    f.link("save", "end")
    return f.build([{"name": "журнал", "default": "D:\\Взыскание\\журнал.xlsx"}], [])


@template("bank", "payroll_payments_file", "Реестр выплат Excel → платёжки 1С → Яндекс Диск", "Банк",
          "Читает реестр выплат из Excel, проверяет ключ счёта по БИК, формирует файл платёжных поручений "
          "1CClientBankExchange, кладёт его на Яндекс Диск и сообщает в Мессенджер.", 45,
          ["платежи", "1С", "Яндекс Диск"])
def _():
    f = Flow()
    f.node("book", "excel.open", "Реестр выплат", 1, 0, path="{{ реестр }}", book="реестр")
    f.node("rows", "excel.read_table", "Строки реестра", 2, 0, book="реестр", sheet="Выплаты", result="выплаты")
    f.node("chk", "core.python", "Проверить счета", 3, 0,
           code="from rpa_engine.banking import check_account, check_inn\n"
                "ошибки = [в for в in выплаты if not check_account(в['Счёт'], в['БИК']) or not check_inn(в['ИНН'])]\n"
                "платежи = [{'Сумма': в['Сумма'], 'Получатель': в['Получатель'], 'ПолучательИНН': в['ИНН'],\n"
                "            'ПолучательСчет': в['Счёт'], 'ПолучательБИК': в['БИК'],\n"
                "            'НазначениеПлатежа': в['Назначение']} for в in выплаты if в not in ошибки]")
    f.node("if", "core.if", "Есть ошибки?", 4, 0, condition="ошибки")
    f.node("stop", "core.fail", "Остановить", 4, 1, message="Ошибки в реквизитах: {{ [о['Получатель'] for о in ошибки] }}")
    f.node("file", "bank.write_1c", "Файл платёжек", 5, 0, payments="платежи", account="{{ счёт_банка }}",
           path="{{ папка }}\\1c_to_kl.txt")
    f.node("disk", "yadisk.upload", "На Яндекс Диск", 6, 0, token="yandex_oauth", file="{{ папка }}\\1c_to_kl.txt",
           path="disk:/Платежи/{{ today().isoformat() }}/")
    f.node("msg", "yandex.messenger", "Сообщить", 6, 1, token="yandex_bot", chat_id="{{ чат }}",
           text="Сформировано платёжек: {{ len(платежи) }} на сумму {{ sum(п['Сумма'] for п in платежи) }} руб.")
    end(f, 7, 1)
    f.chain("start", "book", "rows", "chk", "if")
    f.link("if", "stop", "true")
    f.link("if", "file", "false")
    f.chain("file", "disk", "msg", "end")
    return f.build([{"name": "реестр", "default": "D:\\Выплаты\\реестр.xlsx"},
                    {"name": "счёт_банка", "default": "40702810900000000001"},
                    {"name": "папка", "default": "D:\\Выплаты"}, {"name": "чат", "default": "0/0/xxx"}], [])


@template("bank", "regulatory_report_s3", "Ежедневная отчётность в Object Storage", "Банк",
          "Собирает данные отчёта SQL-запросом, сохраняет в Excel, загружает в Yandex Object Storage и "
          "отправляет временную ссылку в Telegram.", 40, ["отчётность", "SQL", "Yandex Object Storage"])
def _():
    f = Flow()
    f.node("q", "db.query", "Данные отчёта", 1, 0, connection="postgresql+psycopg://rpa:{password}@dwh/dwh",
           password="dwh_password",
           sql="SELECT branch, product, SUM(balance) AS balance FROM balances WHERE dt = CURRENT_DATE - 1 "
               "GROUP BY branch, product ORDER BY branch", result="данные")
    f.node("book", "excel.open", "Книга", 2, 0, path="{{ файл }}", book="отчёт", create=True)
    f.node("w", "excel.write_table", "Таблица", 3, 0, book="отчёт", sheet="Остатки", rows="данные")
    f.node("save", "excel.save", "Сохранить", 4, 0, book="отчёт")
    f.node("up", "s3.upload", "В Object Storage", 1, 1, access_key="YCAJ...", secret_key="yandex_s3_secret",
           bucket="bank-reports", file="{{ файл }}", key="daily/{{ today().isoformat() }}/остатки.xlsx", result="ключ")
    f.node("link", "s3.link", "Временная ссылка", 2, 1, access_key="YCAJ...", secret_key="yandex_s3_secret",
           bucket="bank-reports", key="{{ ключ }}", expires=86400, result="ссылка")
    f.node("tg", "notify.telegram", "Ссылка в Telegram", 3, 1, token="telegram_bot", chat_id="{{ чат }}",
           text="Отчёт об остатках за вчера: {{ ссылка }}")
    end(f, 4, 1)
    f.chain("start", "q", "book", "w", "save", "up", "link", "tg", "end")
    return f.build([{"name": "файл", "default": "D:\\Отчёты\\остатки.xlsx"}, {"name": "чат", "default": "-100123"}], [])


@template("bank", "supplier_invoices_ocr", "Счета поставщиков: OCR → 1С", "Банк",
          "Распознаёт счета на оплату из папки сканов по шаблону «Счёт на оплату», проверяет ИНН и БИК и "
          "создаёт документ «Счёт поставщика» в 1С через OData. Сомнительные — на проверку.", 10,
          ["OCR", "1С", "АХО"], ["Tesseract"])
def _():
    f = Flow()
    f.node("ls", "files.list", "Сканы счетов", 1, 0, folder="{{ папка }}", pattern="*.*", result="сканы")
    f.node("loop", "core.for_each", "По каждому", 2, 0, collection="сканы", item="скан")
    f.node("ocr", "ocr.extract", "Счёт на оплату", 2, 1, file="{{ скан }}", template="invoice", engine="tesseract",
           ai_fallback=False, result="счёт")
    f.node("one", "onec.create", "Документ в 1С", 3, 1, base_url="{{ odata_1c }}", username="robot",
           password="onec_password", entity="Document_СчетНаОплатуПоставщика",
           data="{{ {'НомерВходящегоДокумента': счёт['fields']['number'], "
                "'СуммаДокумента': счёт['fields']['total'], 'Комментарий': 'RPA: ' + скан} }}")
    f.node("rev", "queue.add", "На проверку", 3, 2, queue="Проверка документов",
           data="{{ {'файл': скан, 'распознано': счёт['fields'], 'нет': счёт['missing']} }}", reference="{{ скан }}")
    end(f, 3, 0)
    f.chain("start", "ls", "loop")
    f.link("loop", "ocr", "body")
    f.link("ocr", "one", "ok")
    f.link("ocr", "rev", "review")
    f.link("one", "rev", "error")
    f.link("loop", "end", "done")
    return f.build([{"name": "папка", "default": "D:\\Сканы\\счета"},
                    {"name": "odata_1c", "default": "http://1c-srv/buh/odata/standard.odata"}], [])


# ================================================================ ОБЩИЕ
@template("general", "calculator_macos", "Калькулятор macOS по селекторам", "Примеры",
          "Демонстрация Desktop-блоков: запускает Калькулятор, нажимает кнопки по идентификаторам элементов "
          "(Accessibility) и читает результат с экрана.", 1, ["Desktop", "macOS"])
def _():
    return json.loads((ROOT / "examples" / "calculator_selectors.json").read_text(encoding="utf-8"))


@template("general", "web_portal_login", "Вход на портал и выгрузка таблицы", "Примеры",
          "Открывает портал, входит под учётной записью робота (пароль — из хранилища секретов), "
          "забирает таблицу и сохраняет в Excel. При ошибке входа — скриншот.", 15, ["Web", "Excel"])
def _():
    f = Flow()
    f.node("open", "web.open", "Открыть браузер", 1, 0, url="{{ портал }}/login", headless=True)
    f.node("login", "web.fill", "Логин", 2, 0, selector="#username", value="{{ логин }}")
    f.node("pwd", "web.fill", "Пароль", 3, 0, selector="#password", value="{{ secret('portal_password') }}")
    f.node("go", "web.click", "Войти", 4, 0, selector="role=button[name=\"Войти\"]")
    f.node("tbl", "web.extract_table", "Таблица", 1, 1, selector="table", result="строки")
    f.node("book", "excel.open", "Книга", 2, 1, path="{{ файл }}", book="выгрузка", create=True)
    f.node("w", "excel.write_table", "Записать", 3, 1, book="выгрузка", sheet="Данные", rows="строки")
    f.node("save", "excel.save", "Сохранить", 4, 1, book="выгрузка")
    f.node("shot", "web.screenshot", "Скриншот ошибки", 4, 2, path="ошибка_входа.png")
    f.node("close", "web.close", "Закрыть браузер", 5, 1)
    end(f, 6, 1)
    f.chain("start", "open", "login", "pwd", "go", "tbl", "book", "w", "save", "close", "end")
    f.link("go", "shot", "error")
    f.link("shot", "close")
    return f.build([{"name": "портал", "default": "https://portal.example.com"},
                    {"name": "логин", "default": "robot"}, {"name": "файл", "default": "выгрузка.xlsx"}], ["строки"])


def main() -> int:
    bad = 0
    for t in TEMPLATES:
        errors = validate(t["definition"])
        if errors:
            bad += 1
            print("✗", t["slug"], errors)
        out = ROOT / "templates" / t["group"] / f"{t['slug']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in t.items() if k not in ("group", "slug")}
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("✓" if not errors else "!", out.relative_to(ROOT))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
