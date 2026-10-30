"""Агент D «Проверяющий»: запускает код студента на тестах задачи.

Вердикт даёт запуск тестов, а не мнение модели. Результат: CheckVerdict.

Формат задачи (tasks.json):
    {"task_id": "...", "entry_point": "имя_функции",
     "statement": "...", "tests": ["assert f(1) == 2", ...]}

ВАЖНО: это не настоящая песочница. Код запускается отдельным процессом с
таймаутом, ограничением процессорного времени и чистым окружением, но он
может читать файлы пользователя. Поэтому для демо запускаем только задачи
из нашего набора, а не произвольный код из интернета.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from schemas.messages import CheckRequest, CheckVerdict

# Задачи лежат в benchmarks/datasets: tasks_sample.json (примеры для тестов) и tasks.json (набор Олега)
DATASETS = Path(__file__).resolve().parents[2] / "benchmarks" / "datasets"
TASK_FILES = ("tasks_sample.json", "tasks.json")

# Код-раннер, выполняется в отдельном процессе. Результат печатается JSON-ом
# после маркера, чтобы print() студента его не ломал.
_RUNNER = r"""
import ast, json, sys, traceback
MARK = "@@RESULT@@"
code = open(sys.argv[1], encoding="utf-8").read()
tests = json.load(open(sys.argv[2], encoding="utf-8"))
ns = {}
out = {"error_type": None, "traceback_short": None, "failed": [], "details": []}


def got_value(t):
    # для теста вида "assert <выражение> == <ожидаемое>" берём фактическое значение выражения
    try:
        node = ast.parse(t).body[0]
        if (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)
                and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)):
            expr = ast.Expression(node.test.left)
            return repr(eval(compile(expr, "<test>", "eval"), ns))[:200]
    except BaseException:
        pass
    return None

try:
    exec(compile(code, "<student>", "exec"), ns)
except BaseException as e:
    out["error_type"] = type(e).__name__
    out["traceback_short"] = "".join(traceback.format_exception_only(type(e), e)).strip()[:300]
else:
    for t in tests:
        try:
            exec(t, ns)
        except AssertionError:
            out["failed"].append(t)
            out["details"].append({"test": t, "got": got_value(t)})
        except BaseException as e:
            out["failed"].append(t)
            out["details"].append({"test": t, "got": None})
            if out["error_type"] is None:
                out["error_type"] = type(e).__name__
                out["traceback_short"] = "".join(traceback.format_exception_only(type(e), e)).strip()[:300]
print(MARK + json.dumps(out, ensure_ascii=False))
"""


def load_tasks(folder: Path = DATASETS) -> dict[str, dict]:
    """Собирает задачи из всех файлов TASK_FILES, которые есть в папке."""
    tasks: dict[str, dict] = {}
    for name in TASK_FILES:
        path = Path(folder) / name
        if path.exists():
            for t in json.loads(path.read_text(encoding="utf-8")):
                tasks[t["task_id"]] = t
    return tasks


def _limits():  # выполняется в дочернем процессе до запуска кода
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    except Exception:
        pass


def run_tests(task: dict, student_code: str, timeout: float = 5.0) -> CheckVerdict:
    tid = task["task_id"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "runner.py").write_text(_RUNNER, encoding="utf-8")
        (tmp_path / "student.py").write_text(student_code, encoding="utf-8")
        (tmp_path / "tests.json").write_text(json.dumps(task["tests"]), encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "runner.py", "student.py", "tests.json"],
                cwd=tmp, capture_output=True, text=True, timeout=timeout,
                env={}, preexec_fn=_limits if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired:
            return CheckVerdict(task_id=tid, passed=False, error_type="Timeout",
                                traceback_short=f"Код работает дольше {timeout} с")

    marker = "@@RESULT@@"
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(marker)), None)
    if line is None:  # процесс упал до вывода результата (например, по лимиту CPU)
        return CheckVerdict(task_id=tid, passed=False, error_type="RuntimeCrash",
                            traceback_short=(proc.stderr or "нет вывода")[-300:])
    res = json.loads(line[len(marker):])
    failed = res["failed"]
    if res["error_type"] is None and not failed:
        return CheckVerdict(task_id=tid, passed=True)
    return CheckVerdict(
        task_id=tid, passed=False, failed_tests=failed,
        failed_details=res.get("details", []),
        error_type=res["error_type"] or "AssertionError",
        traceback_short=res["traceback_short"],
    )


def check(request: CheckRequest, tasks: dict[str, dict] | None = None) -> CheckVerdict:
    """Точка входа для оркестратора: CheckRequest -> CheckVerdict."""
    tasks = tasks or load_tasks()
    task = tasks.get(request.task_id)
    if task is None:
        return CheckVerdict(task_id=request.task_id, passed=False,
                            error_type="UnknownTask",
                            traceback_short=f"Нет задачи {request.task_id}")
    return run_tests(task, request.student_code)
