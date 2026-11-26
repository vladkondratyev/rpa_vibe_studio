"""Командная строка оркестратора.

  python -m orchestrator create-user admin --role admin       # пароль спросит
  python -m orchestrator export ./processes [--git]            # процессы в JSON-файлы (+ коммит в Git)
  python -m orchestrator import ./processes                    # загрузить JSON-файлы процессов
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from .models import Process, ProcessVersion, SessionLocal, User, init_db
from .security import ALL_ROLES, hash_password


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("create-user", help="создать пользователя или сменить пароль")
    u.add_argument("username")
    u.add_argument("--role", default="admin", choices=ALL_ROLES)
    u.add_argument("--password", help="если не указан — будет запрошен")
    e = sub.add_parser("export", help="выгрузить процессы в папку (Git-версионирование)")
    e.add_argument("folder")
    e.add_argument("--git", action="store_true", help="сделать коммит, если папка — Git-репозиторий")
    i = sub.add_parser("import", help="загрузить процессы из папки")
    i.add_argument("folder")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    try:
        if args.cmd == "create-user":
            password = args.password or getpass.getpass("Пароль: ")
            user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
            if user is None:
                user = User(username=args.username)
                db.add(user)
            user.role, user.password_hash, user.active, user.source = args.role, hash_password(password), True, "local"
            db.commit()
            print(f"Пользователь {args.username} ({args.role}) сохранён")
        elif args.cmd == "export":
            folder = Path(args.folder)
            folder.mkdir(parents=True, exist_ok=True)
            for p in db.execute(select(Process)).scalars():
                name = re.sub(r"[^\w\-]+", "_", p.name).strip("_") or f"process_{p.id}"
                data = {"format": "rpa-vibe-studio/process@1", "name": p.name, "description": p.description,
                        "minutes_saved": p.minutes_saved or 0, "version": p.version, "definition": p.definition}
                (folder / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
                print("→", name + ".json")
            if args.git:
                subprocess.run(["git", "-C", str(folder), "add", "-A"], check=True)
                r = subprocess.run(["git", "-C", str(folder), "commit", "-m", "RPA Vibe Studio: выгрузка процессов"])
                return 0 if r.returncode in (0, 1) else r.returncode
        elif args.cmd == "import":
            for path in sorted(Path(args.folder).glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                p = db.execute(select(Process).where(Process.name == data["name"])).scalar_one_or_none()
                if p is None:
                    p = Process(name=data["name"], definition=data["definition"], version=1)
                    db.add(p)
                    db.flush()
                elif p.definition != data["definition"]:
                    p.definition, p.version = data["definition"], p.version + 1
                else:
                    continue
                p.description, p.minutes_saved = data.get("description", ""), data.get("minutes_saved", 0)
                db.add(ProcessVersion(process_id=p.id, version=p.version, definition=p.definition, author="import"))
                db.commit()
                print("←", path.name, f"v{p.version}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
