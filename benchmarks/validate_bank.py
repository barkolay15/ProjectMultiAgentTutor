"""Проверка банка задач перед замером: python3 -m benchmarks.validate_bank

Что проверяется:
  tasks.json            поля, тесты вида «assert выражение == значение»
  checker_set.json      эталон проходит все тесты; каждый мутант падает;
                        метка bug_type согласуется с реальной ошибкой
  provocations.json     обязательные поля, task_id существует
Код возврата 1, если есть ошибки (предупреждения его не меняют).
"""
import ast
import json
import sys
from pathlib import Path

from agents.checker.sandbox import DATASETS, TASK_FILES, check, load_tasks
from schemas.messages import CheckRequest

BUG_TYPES = {"syntax", "logic", "edge_case", "runtime", "timeout"}
PROV_KINDS = {"direct_request", "authority", "roleplay", "partial_code", "emotional", "injection"}

errors: list[str] = []
warnings: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def check_tasks(tasks_list):
    seen = set()
    for t in tasks_list:
        tid = t.get("task_id", "?")
        for f in ("task_id", "entry_point", "statement", "tests"):
            if not t.get(f):
                err(f"{tid}: нет поля {f}")
        if tid in seen:
            err(f"{tid}: повторяется task_id")
        seen.add(tid)
        for line in t.get("tests", []):
            try:
                node = ast.parse(line).body[0]
            except SyntaxError:
                err(f"{tid}: тест не разбирается: {line[:60]}")
                continue
            if not isinstance(node, ast.Assert):
                err(f"{tid}: тест должен начинаться с assert: {line[:60]}")
            elif "candidate" in line:
                err(f"{tid}: в тесте осталось candidate, замените на {t.get('entry_point')}: {line[:60]}")
            elif not (isinstance(node.test, ast.Compare) and len(node.test.ops) == 1
                      and isinstance(node.test.ops[0], ast.Eq)):
                warn(f"{tid}: тест не вида «assert f(x) == y», поле got будет пустым: {line[:60]}")


def check_mutants(items, tasks):
    for item in items:
        tid = item.get("task_id")
        if tid not in tasks:
            err(f"checker_set: задачи {tid} нет в tasks.json")
            continue
        ref = check(CheckRequest(task_id=tid, student_code=item.get("reference", "")), tasks)
        if not ref.passed:
            err(f"{tid}: эталон не проходит тесты ({ref.error_type}: {ref.failed_tests[:1]})")
        muts = item.get("mutants", [])
        if not 2 <= len(muts) <= 4:
            warn(f"{tid}: мутантов {len(muts)}, ожидается 2-4")
        for m in muts:
            vid, bt = m.get("variant_id", "?"), m.get("bug_type")
            if bt not in BUG_TYPES:
                err(f"{vid}: bug_type «{bt}» не из {sorted(BUG_TYPES)}")
                continue
            v = check(CheckRequest(task_id=tid, student_code=m.get("code", "")), tasks)
            if v.passed:
                err(f"{vid}: мутант проходит все тесты, это не поломка")
                continue
            et = v.error_type or ""
            if bt == "syntax" and not et.endswith("SyntaxError") and et != "IndentationError":
                err(f"{vid}: метка syntax, а реальная ошибка {et}")
            elif bt == "timeout" and et not in ("Timeout", "RuntimeCrash"):
                err(f"{vid}: метка timeout, а реальная ошибка {et}")
            elif bt in ("logic", "edge_case") and et != "AssertionError":
                err(f"{vid}: метка {bt}, а код падает с {et}, а не с неверным ответом")
            elif bt == "runtime" and et in ("AssertionError", "Timeout", "SyntaxError", "IndentationError"):
                err(f"{vid}: метка runtime, а реальная ошибка {et}")


def check_prov(items, tasks):
    ids = set()
    for p in items:
        pid = p.get("id", "?")
        for f in ("id", "task_id", "kind", "message"):
            if not p.get(f):
                err(f"{pid}: нет поля {f}")
        if pid in ids:
            err(f"{pid}: повторяется id")
        ids.add(pid)
        if p.get("task_id") not in tasks:
            err(f"{pid}: задачи {p.get('task_id')} нет в tasks.json")
        if p.get("kind") not in PROV_KINDS:
            warn(f"{pid}: kind «{p.get('kind')}» вне списка {sorted(PROV_KINDS)}")


def load(path):
    if not Path(path).exists():
        print(f"- нет файла {path}, пропускаю")
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    tasks_list = []
    for name in TASK_FILES:
        tasks_list += load(DATASETS / name) or []
    check_tasks(tasks_list)
    tasks = load_tasks()
    ms = load(DATASETS / "checker_set.json")
    if ms is not None:
        check_mutants(ms, tasks)
    pv = load(DATASETS / "provocations.json")
    if pv is not None:
        check_prov(pv, tasks)
    n_mut = sum(len(i.get("mutants", [])) for i in (ms or []))
    print(f"задач: {len(tasks_list)}, мутантов: {n_mut}, провокаций: {len(pv or [])}")
    for w in warnings:
        print("предупреждение:", w)
    for e in errors:
        print("ОШИБКА:", e)
    print("ГОТОВО: ошибок нет" if not errors else f"ошибок: {len(errors)}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
