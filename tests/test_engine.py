from __future__ import annotations

import os

from rpa_engine import Context, run_process, validate


def proc(nodes, edges, **extra):
    d = {"nodes": [{"id": n[0], "type": n[1], "params": n[2] if len(n) > 2 else {}} for n in nodes],
         "edges": [{"source": e[0], "sourceHandle": e[1], "target": e[2]} for e in edges]}
    d.update(extra)
    return d


def run(d, inputs=None):
    logs = []
    ctx = Context(log=lambda lvl, msg, node: logs.append((lvl, msg, node)))
    res = run_process(d, inputs, ctx)
    return res, ctx, logs


def test_variables_templates_and_outputs():
    d = proc(
        [("s", "core.start"),
         ("a", "core.set_variable", {"name": "x", "value": "{{ 2 + 3 }}"}),
         ("b", "core.set_variable", {"name": "msg", "value": "x={{ x }}, имя={{ name }}"}),
         ("e", "core.end")],
        [("s", "out", "a"), ("a", "out", "b"), ("b", "out", "e")],
        variables=[{"name": "name", "default": "мир"}], outputs=["x", "msg"],
    )
    res, _, _ = run(d)
    assert res.status == "success", res.error
    assert res.outputs == {"x": 5, "msg": "x=5, имя=мир"}


def test_inputs_override_defaults():
    d = proc([("s", "core.start"), ("e", "core.end")], [("s", "out", "e")],
             variables=[{"name": "n", "default": 1}], outputs=["n"])
    assert run(d, {"n": 7})[0].outputs == {"n": 7}


def test_if_branches():
    d = proc(
        [("s", "core.start"), ("c", "core.if", {"condition": "n > 0"}),
         ("y", "core.set_variable", {"name": "r", "value": "плюс"}),
         ("no", "core.set_variable", {"name": "r", "value": "минус"})],
        [("s", "out", "c"), ("c", "true", "y"), ("c", "false", "no")], outputs=["r"],
    )
    assert run(d, {"n": 1})[0].outputs == {"r": "плюс"}
    assert run(d, {"n": -1})[0].outputs == {"r": "минус"}


def test_for_each_auto_returns_and_nested_loops():
    d = proc(
        [("s", "core.start"),
         ("init", "core.python", {"code": "total = 0\npairs = []"}),
         ("outer", "core.for_each", {"collection": "[1, 2, 3]", "item": "i"}),
         ("inner", "core.for_each", {"collection": "['a', 'b']", "item": "j"}),
         ("add", "core.python", {"code": "total += i\npairs.append(f'{i}{j}')"}),
         ("e", "core.end")],
        [("s", "out", "init"), ("init", "out", "outer"), ("outer", "body", "inner"),
         ("inner", "body", "add"), ("outer", "done", "e")],
        outputs=["total", "pairs"],
    )
    res, _, _ = run(d)
    assert res.status == "success", res.error
    assert res.outputs["total"] == 12
    assert res.outputs["pairs"] == ["1a", "1b", "2a", "2b", "3a", "3b"]


def test_while_loop():
    d = proc(
        [("s", "core.start"), ("w", "core.while", {"condition": "k < 5"}),
         ("inc", "core.python", {"code": "k += 1"}), ("e", "core.end")],
        [("s", "out", "w"), ("w", "body", "inc"), ("inc", "out", "w"), ("w", "done", "e")],
        variables=[{"name": "k", "default": 0}], outputs=["k"],
    )
    assert run(d)[0].outputs == {"k": 5}


def test_error_without_branch_fails_with_node():
    d = proc([("s", "core.start"), ("bad", "core.python", {"code": "1/0"})], [("s", "out", "bad")])
    res, _, logs = run(d)
    assert res.status == "failed" and res.failed_node == "bad"
    assert "division by zero" in res.error
    assert any(l[0] == "error" for l in logs)


def test_error_branch_catches():
    d = proc(
        [("s", "core.start"), ("bad", "core.fail", {"message": "нет данных"}),
         ("p", "core.python", {"code": "1/0"}),
         ("h", "core.set_variable", {"name": "handled", "value": "{{ last_error }}"})],
        [("s", "out", "p"), ("p", "error", "h")], outputs=["handled"],
    )
    res, _, _ = run(d)
    assert res.status == "success"
    assert "division by zero" in res.outputs["handled"]


def test_python_block_does_not_leak_imports_and_functions():
    d = proc([("s", "core.start"),
              ("p", "core.python", {"code": "import os as _o\nimport math\ndef f(x):\n    return x*2\ny = f(21)"})],
             [("s", "out", "p")])
    _, ctx, _ = run(d)
    assert ctx.vars["y"] == 42
    assert "f" not in ctx.vars and "math" not in ctx.vars


def test_validate_reports_problems():
    d = proc([("a", "core.log", {}), ("b", "nope.block")], [("a", "out", "zzz")])
    errors = validate(d)
    text = "\n".join(errors)
    assert "Старт" in text and "nope.block" in text and "Сообщение" in text and "несуществующий" in text
    assert run_process(d).status == "failed"


def test_cancel():
    d = proc([("s", "core.start"), ("w", "core.delay", {"seconds": 5})], [("s", "out", "w")])
    ctx = Context()
    ctx.cancel_event.set()
    assert run_process(d, None, ctx).status == "cancelled"


def test_files_and_excel(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for i in range(3):
        (src / f"f{i}.txt").write_text(str(i * 10), encoding="utf-8")
    out_xlsx = str(tmp_path / "out" / "report.xlsx")
    d = proc(
        [("s", "core.start"),
         ("ls", "files.list", {"folder": str(src), "pattern": "*.txt", "result": "files"}),
         ("open", "excel.open", {"path": out_xlsx, "create": True}),
         ("head", "excel.append_row", {"values": "['файл', 'число']"}),
         ("loop", "core.for_each", {"collection": "files", "item": "path"}),
         ("read", "files.read_text", {"path": "{{ path }}", "result": "txt"}),
         ("row", "excel.append_row", {"values": "[os.path.basename(path), int(txt)]"}),
         ("save", "excel.save", {}),
         ("reopen", "excel.open", {"path": out_xlsx, "book": "check"}),
         ("table", "excel.read_table", {"book": "check", "result": "rows"}),
         ("e", "core.end")],
        [("s", "out", "ls"), ("ls", "out", "open"), ("open", "out", "head"), ("head", "out", "loop"),
         ("loop", "body", "read"), ("read", "out", "row"), ("loop", "done", "save"),
         ("save", "out", "reopen"), ("reopen", "out", "table"), ("table", "out", "e")],
        outputs=["rows"],
    )
    res, _, _ = run(d)
    assert res.status == "success", res.error
    assert res.outputs["rows"] == [{"файл": "f0.txt", "число": 0}, {"файл": "f1.txt", "число": 10},
                                   {"файл": "f2.txt", "число": 20}]
    assert os.path.exists(out_xlsx)


def test_template_with_two_expressions():
    ctx = Context({"a": 2, "b": 3})
    assert ctx.render("{{ a }} = {{ b }}") == "2 = 3"
    assert ctx.render("{{ [a, b] }}") == [2, 3]
