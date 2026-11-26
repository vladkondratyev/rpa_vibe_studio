"""Командная строка робота.

  Подключиться к оркестратору:
    python -m rpa_robot agent --server http://orch:8000 --name robot-01 --tags web,excel
  Запустить процесс локально из файла (отладка):
    python -m rpa_robot run process.json --var folder=/tmp/in --var limit=10
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

    args = parser.parse_args()
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
