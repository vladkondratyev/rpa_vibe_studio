"""Командная строка робота.

  Подключиться к оркестратору:
    python -m rpa_robot agent --server http://orch:8000 --name robot-01 --tags web,excel
  Запустить процесс локально из файла (отладка):
    python -m rpa_robot run process.json --var folder=/tmp/in --var limit=10
  Записать действия в браузере и получить процесс:
    python -m rpa_robot record https://portal.example.com --out process.json
  Посмотреть элементы окна приложения и готовые селекторы:
    python -m rpa_robot inspect --app Calculator
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys

from .agent import Agent


def _parse_var(raw: str):
    name, _, value = raw.partition("=")
    try:
        return name, json.loads(value)
    except ValueError:
        return name, value


def main() -> int:
    parser = argparse.ArgumentParser(prog="rpa_robot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("agent", help="подключиться к оркестратору и выполнять задачи")
    a.add_argument("--server", default=os.environ.get("RPA_SERVER", "http://localhost:8000"))
    a.add_argument("--name", default=os.environ.get("RPA_ROBOT_NAME", platform.node()))
    a.add_argument("--tags", default=os.environ.get("RPA_ROBOT_TAGS", ""), help="через запятую")
    a.add_argument("--token", default=os.environ.get("RPA_TOKEN", ""))

    r = sub.add_parser("run", help="выполнить процесс из JSON-файла локально")
    r.add_argument("file")
    r.add_argument("--var", action="append", default=[], help="имя=значение (значение — JSON или текст)")

    rec = sub.add_parser("record", help="записать действия в браузере и превратить их в процесс")
    rec.add_argument("url")
    rec.add_argument("--out", help="сохранить процесс в JSON-файл")
    rec.add_argument("--server", default=os.environ.get("RPA_SERVER"), help="сразу создать процесс в оркестраторе")
    rec.add_argument("--token", default=os.environ.get("RPA_TOKEN", ""))
    rec.add_argument("--name", default="Записанный процесс")
    rec.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])

    ins = sub.add_parser("inspect", help="дерево элементов окна с селекторами (Windows / macOS)")
    ins.add_argument("--app", default="")
    ins.add_argument("--window", default="")
    ins.add_argument("--depth", type=int, default=6)

    args = parser.parse_args()
    if args.cmd == "record":
        from .recorder import main as record_main

        return record_main(args)
    if args.cmd == "inspect":
        from rpa_engine.blocks.desktop import inspect_tree

        for it in inspect_tree(args.app, args.window, args.depth):
            print("  " * it["depth"] + it["selector"])
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "agent":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        try:
            Agent(args.server, args.name, tags, args.token).run_forever()
        except KeyboardInterrupt:
            pass
        return 0

    from rpa_engine import run_file

    result = run_file(args.file, dict(_parse_var(v) for v in args.var))
    print(json.dumps({"status": result.status, "error": result.error, "outputs": result.outputs},
                     ensure_ascii=False, indent=2, default=str))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
