"""Чистые функции интерфейса: подписи, разбор ответов API, экспорт диалога.

Здесь нет ни streamlit, ни сети, поэтому всё тестируется без запуска приложения
(tests/test_frontend_helpers.py).
"""
import json
import re

MAX_HINT = 3
CHECK_CODE_MESSAGE = "Я отправил своё решение на проверку. Что показали тесты и что мне делать дальше?"
EXAMPLE_PROMPTS = ("Не понимаю задачу", "Дай подсказку", "С чего начать?")

CATEGORY_LABELS = {
    "syntax": "синтаксис",
    "logic": "логика",
    "edge_case": "граничный случай",
    "runtime": "ошибка выполнения",
    "timeout": "превышено время",
    "other": "другое",
}
NODE_LABELS = {
    "load_profile": "Профиль из памяти",
    "pick_task": "Выбор задачи",
    "check": "Проверка кода",
    "review": "Разбор ошибки",
    "tutor": "Ответ тьютора",
    "update_memory": "Запись в память",
    "finish": "Завершение",
}
STATUS_LABELS = {"ok": "всё хорошо", "error": "ошибка", "step_limit": "лимит шагов"}


BUG_TYPE_LABELS = {
    "logic": "ошибка в логике",
    "syntax": "синтаксическая ошибка",
    "runtime": "исключение при запуске",
    "edge_case": "граничный случай",
    "timeout": "зависание",
}

_STATEMENT_RE = re.compile(
    r"^(?P<description>.*?)\s*Функция должна называться (?P<entry>\w+)\.\s*Пример проверки:\s*(?P<example>.*)$",
    re.DOTALL,
)


def parse_statement(statement: str) -> dict:
    """Условие из банка: описание, имя функции и пример проверки. Если формат другой, всё условие идёт в описание."""
    m = _STATEMENT_RE.match((statement or "").strip())
    if not m:
        return {"description": (statement or "").strip(), "entry_point": None, "example": None}
    return {"description": m["description"].strip(), "entry_point": m["entry"], "example": m["example"].strip()}


def task_option_label(task: dict, solved: set | list | None = None, width: int = 60) -> str:
    """Подпись задачи в списке выбора: отметка о решении, имя функции и начало описания."""
    done = "✅ " if task.get("task_id") in (solved or ()) else ""
    desc = parse_statement(task.get("statement", ""))["description"]
    if len(desc) > width:
        desc = desc[: width - 1].rstrip() + "…"
    name = task.get("entry_point") or task.get("task_id", "")
    return f"{done}{name}: {desc}" if desc else f"{done}{name}"


def solved_summary(solved, total: int) -> str:
    return f"Решено {len(set(solved or []))} из {total}"


def bug_type_label(bug_type: str | None) -> str:
    return BUG_TYPE_LABELS.get(bug_type or "", bug_type or "другое")


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", category or "не определена")


def node_label(node: str) -> str:
    return NODE_LABELS.get(node, node)


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "неизвестно")


def clamp_hint(level) -> int:
    try:
        return max(1, min(MAX_HINT, int(level)))
    except (TypeError, ValueError):
        return 1


def hint_text(level) -> str:
    return f"Подсказка {clamp_hint(level)} из {MAX_HINT}"


def verdict_info(verdict: dict | None) -> dict | None:
    """Короткая подпись вердикта: {"kind": "pass" | "fail", "icon", "text", "color"}; None, если кода не было."""
    if not verdict:
        return None
    if verdict.get("passed"):
        return {"kind": "pass", "icon": "✅", "text": "Тесты прошли", "color": "green"}
    return {"kind": "fail", "icon": "❌", "text": "Тесты не прошли", "color": "red"}


def timings_table(timings: list[dict] | None) -> list[dict]:
    """Строки таблицы времени по шагам графа: название, секунды, доля от суммы."""
    timings = timings or []
    total = sum(t.get("seconds", 0) for t in timings)
    rows = []
    for t in timings:
        sec = t.get("seconds", 0)
        rows.append({
            "Шаг": node_label(t.get("node", "")),
            "Время, с": round(sec, 3),
            "Доля, %": round(100 * sec / total) if total > 0 else 0,
        })
    return rows


def slowest_step(timings: list[dict] | None) -> str | None:
    if not timings:
        return None
    t = max(timings, key=lambda x: x.get("seconds", 0))
    return f"{node_label(t.get('node', ''))} ({t.get('seconds', 0):.2f} с)"


def pipeline_line(timings: list[dict] | None) -> str:
    """Путь хода по узлам графа с временем, например: «Профиль из памяти 0.00 с → Проверка кода 0.41 с»."""
    steps = [t for t in (timings or []) if t.get("node") != "finish"]
    return " → ".join(f"{node_label(t.get('node', ''))} {t.get('seconds', 0):.2f} с" for t in steps)


def step_line(event: dict) -> str:
    """Строка о завершённом шаге хода для индикатора прогресса."""
    return f"✅ {node_label(event.get('node', ''))}: {event.get('seconds', 0):.2f} с"


def parse_stream_line(line) -> dict | None:
    """Одна строка потока /chat/stream (NDJSON) в событие; пустые и битые строки пропускаются (None)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = (line or "").strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


def attempts_table(chat: list[dict]) -> list[dict]:
    """Попытки студента: каждое сообщение с кодом и вердикт проверки, который за ним последовал."""
    rows = []
    for i, item in enumerate(chat):
        if item.get("role") != "user" or not item.get("code"):
            continue
        reply = chat[i + 1] if i + 1 < len(chat) and chat[i + 1].get("role") == "assistant" else {}
        d = reply.get("details") or {}
        v = verdict_info(d.get("verdict"))
        rows.append({
            "№": len(rows) + 1,
            "Задача": (d.get("task") or {}).get("task_id", ""),
            "Результат": f"{v['icon']} {v['text']}" if v else "нет ответа",
            "Время, с": round(d.get("total_seconds", 0), 1) if d else None,
        })
    return rows


def connection_error_text(kind: str, base_url: str) -> str:
    if kind == "timeout":
        return ("Сервер не ответил вовремя. Возможно, модель отвечает долго: "
                "подождите и отправьте сообщение ещё раз.")
    return (f"Не удалось подключиться к API ({base_url}). "
            "Проверьте, что бэкенд запущен: uvicorn api.main:app --reload")


def api_error_text(status_code: int, detail=None) -> str:
    if status_code == 422:
        return ("Сообщение не принято: оно пустое или слишком длинное "
                "(сообщение до 2000 символов, код до 10 000).")
    if status_code == 404:
        return "Нужный адрес API не найден. Проверьте адрес API в настройках и версию бэкенда."
    if status_code >= 500:
        text = detail if isinstance(detail, str) and detail else "Внутренняя ошибка сервера."
        return f"{text} Подробности в терминале с бэкендом."
    return f"Сервер вернул ошибку {status_code}."


def history_to_chat(history: list[dict] | None) -> list[dict]:
    """История сессии с сервера (роли student/tutor) в формат чата интерфейса."""
    roles = {"student": "user", "tutor": "assistant"}
    return [
        {"role": roles[h["role"]], "text": h.get("text", "")}
        for h in (history or [])
        if h.get("role") in roles
    ]


def chat_to_markdown(chat: list[dict], student_id: str) -> str:
    """Диалог в виде Markdown: для скачивания и вставки в отчёт."""
    lines = [f"# Диалог с репетитором (студент {student_id})", ""]
    for item in chat:
        who = "Студент" if item["role"] == "user" else "Репетитор"
        lines.append(f"**{who}:** {item['text']}")
        if item.get("code"):
            lines += ["", "```python", item["code"].rstrip(), "```"]
        d = item.get("details") or {}
        v = verdict_info(d.get("verdict"))
        if v:
            lines.append(f"_{v['icon']} {v['text']}; {hint_text(d.get('hint_level', 1)).lower()}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"