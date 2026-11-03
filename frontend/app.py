"""Интерфейс репетитора (Streamlit): условие сверху, затем чат и редактор с результатом проверки.

Запуск из корня репозитория (бэкенд должен работать: uvicorn api.main:app --reload):
    streamlit run frontend/app.py

Адрес API по умолчанию берётся из переменной TUTOR_API_URL (http://127.0.0.1:8000).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # чтобы работал import frontend.*

import pandas as pd
import streamlit as st

from core.examples import example_from_test
from frontend.client import ApiError, TutorClient
from frontend.editor import code_editor
from frontend.helpers import (
    CHECK_CODE_MESSAGE,
    EXAMPLE_PROMPTS,
    MAX_HINT,
    STATUS_FILTERS,
    attempts_table,
    category_label,
    chat_to_markdown,
    clamp_hint,
    failed_test_details,
    hint_text,
    history_to_chat,
    next_unsolved,
    parse_statement,
    pipeline_line,
    slowest_step,
    solved_summary,
    starter_for,
    status_label,
    step_line,
    task_option_label,
    task_rows,
    timings_table,
    turn_error_text,
    verdict_info,
)
from frontend.style import apply_style

DEFAULT_API_URL = os.getenv("TUTOR_API_URL", "http://127.0.0.1:8000")
AVATARS = {"user": "🧑‍💻", "assistant": "🧑‍🏫"}

st.set_page_config(page_title="Репетитор по Python", page_icon="🐍", layout="wide")
apply_style()

page = st.navigation([
    st.Page(lambda: None, title="Репетитор", icon="🐍", default=True),
    st.Page("pages/1_Замеры.py", title="Замеры", icon="📊"),
])
page.run()
if page.title != "Репетитор":
    st.stop()

state = st.session_state
state.setdefault("chats", {})   # student_id -> сообщения чата
state.setdefault("last", {})    # student_id -> последний ответ API (задача, вердикт, подсказка)
state.setdefault("solved", {})  # student_id -> id решённых задач
state.setdefault("editor", {})  # student_id -> {"ver": номер поля, "seed": начальный код, "task_id": задача}
state.setdefault("drafts", {})  # student_id -> task_id -> код (в пределах сессии браузера)
state.setdefault("results", {})  # student_id -> task_id -> последний результат и проверенный код
state.setdefault("failed_requests", {})


@st.cache_data(ttl=3, show_spinner=False)
def api_status(url: str):
    """(жив ли API, режим работы). Кэш на 3 секунды, чтобы не дёргать API на каждый клик."""
    c = TutorClient(url)
    ok = c.health()
    return ok, (c.info() if ok else None)


@st.cache_data(ttl=60, show_spinner=False)
def bank_tasks(url: str) -> list[dict]:
    return TutorClient(url).tasks()


def reset_editor(student_id: str, seed: str = "", task_id: str | None = None) -> None:
    """Новое поле для кода: меняется ключ виджета, поэтому в него попадает seed (заготовка или пусто)."""
    ed = state.editor.setdefault(student_id, {"ver": 0, "seed": "", "task_id": None})
    ed["ver"] += 1
    ed["seed"] = seed
    ed["task_id"] = task_id
    state.drafts.setdefault(student_id, {})[task_id] = seed


def remember_editor(student_id: str) -> None:
    ed = state.editor.get(student_id)
    if ed:
        code = state.get(f"code_{student_id}_{ed['ver']}", ed["seed"])
        if isinstance(code, str):
            state.drafts.setdefault(student_id, {})[ed["task_id"]] = code


def take_task(student_id: str, task_id: str, starter: str = "") -> bool:
    """Студент выбрал задачу: сервер запоминает её, редактор восстанавливает черновик или заготовку.
    Работает и как on_click (до перерисовки страницы), поэтому берёт всё из аргументов и состояния."""
    remember_editor(student_id)
    try:
        data = TutorClient(state.get("api_url", DEFAULT_API_URL)).set_task(student_id, task_id)
    except ApiError as e:
        state["flash"] = e.message
        return False
    state.last[student_id] = {"task": data["task"], "hint_level": 1}
    seed = state.drafts.get(student_id, {}).get(task_id, data.get("starter") or starter)
    reset_editor(student_id, seed, task_id)
    state.failed_requests.pop(student_id, None)
    return True


def open_task_picker() -> None:
    state["dlg"] = state.get("dlg", 0) + 1
    state["task_picker_open"] = True


def close_task_picker() -> None:
    state["task_picker_open"] = False


def render_task_example(task: dict) -> None:
    example = task.get("example") or example_from_test(parse_statement(task.get("statement", ""))["example"] or "")
    if example:
        st.caption("Пример")
        inputs, expected = st.columns(2)
        inputs.caption("Входные данные")
        inputs.code(example["input"], language="python", wrap_lines=True)
        expected.caption("Ожидаемый результат")
        expected.code(example["output"], language="python", wrap_lines=True)


# ---------------------------------------------------------------- боковая панель, часть 1
api_url = state.get("api_url", DEFAULT_API_URL)
api_ok, info = api_status(api_url)
client = TutorClient(api_url)

with st.sidebar:
    st.title("🐍 Репетитор")
    student_id = st.text_input("ID студента", value="s1", key="student_id", max_chars=64,
                               help="Идентификатор сессии: буквы, цифры, точка, дефис и подчёркивание. Под ним сохраняются диалог и прогресс.").strip() or "s1"
    if api_ok:
        st.markdown(":green[● Сервис на связи]")
    else:
        st.markdown(":red[● Сервис недоступен]")
        st.caption("Запустите бэкенд в соседнем терминале:")
        st.code("uvicorn api.main:app --reload", language="bash")
        if st.button("Проверить связь"):
            api_status.clear()
            st.rerun()

# ---------------------------------------------------------------- состояние студента
if api_ok and student_id not in state.chats:
    try:  # после обновления страницы подтягиваем историю с сервера
        sess = client.session(student_id)
        state.chats[student_id] = history_to_chat(sess.get("history"))
        state.solved[student_id] = sess.get("solved", [])
        if sess.get("task"):
            state.last[student_id] = {"task": sess["task"], "hint_level": sess.get("hint_level", 1)}
    except ApiError:
        pass
chat = state.chats.setdefault(student_id, [])
last = state.last.get(student_id)
solved = state.solved.get(student_id, [])
bank_ok = bool(api_ok and info and info.get("checker") == "real" and info.get("bank"))
bank = bank_tasks(api_url) if bank_ok else []
task_now = (last or {}).get("task") or {}
task_id_now = task_now.get("task_id")

# Если задачу выдал репетитор, восстанавливаем её черновик или подставляем заготовку.
ed = state.editor.setdefault(student_id, {"ver": 0, "seed": "", "task_id": None})
if task_id_now and ed.get("task_id") != task_id_now:
    remember_editor(student_id)
    seed = state.drafts.get(student_id, {}).get(task_id_now, starter_for(bank, task_id_now))
    reset_editor(student_id, seed, task_id_now)
    ed = state.editor[student_id]

# ---------------------------------------------------------------- боковая панель, часть 2
with st.sidebar:
    if info:
        st.caption(f"Модель: {info.get('model') or 'не используется'}")
        st.caption(f"Промпт проверяющего: {info.get('checker_prompt_version') or 'нет данных'}")

    if bank:
        st.progress(len(set(solved)) / len(bank), text=solved_summary(solved, len(bank)))

    with st.popover("Начать заново"):
        st.write("Диалог, выбранная задача и прогресс этого студента будут сброшены.")
        if st.button("Да, начать заново", key="confirm_reset", disabled=not api_ok):
            try:
                client.reset(student_id)
            except ApiError as e:
                state["flash"] = e.message
            else:
                state.chats.pop(student_id, None)
                state.last.pop(student_id, None)
                state.solved.pop(student_id, None)
                state.drafts.pop(student_id, None)
                state.results.pop(student_id, None)
                state.failed_requests.pop(student_id, None)
                reset_editor(student_id)
            st.rerun()

    st.download_button("Скачать диалог (.md)", data=chat_to_markdown(chat, student_id),
                       file_name=f"dialog_{student_id}.md", mime="text/markdown", disabled=not chat)
    st.caption("Тема оформления: меню ⋮ справа вверху, Settings, Theme.")

    with st.expander("Для разработчиков"):
        dev_mode = st.toggle("Показывать работу агентов", value=False, key="dev_mode",
                             help="В карточке результата: вердикт проверяющего, разбор ошибки, время шагов.")
        if info:
            st.caption("Проверяющий: " + ("настоящий" if info.get("checker") == "real" else "заглушка")
                       + " · Тьютор: " + ("GigaChat" if info.get("tutor") == "llm" else "заглушка")
                       + f" · Задач в банке: {info.get('bank', 0)}")
        m = client.metrics() if api_ok else None
        if m and m.get("sessions"):
            c1, c2 = st.columns(2)
            c1.metric("Ходов", m["sessions"])
            c2.metric("Ошибок", f"{round(m['error_rate'] * 100)}%")
            c3, c4 = st.columns(2)
            c3.metric("p50, с", m["p50"])
            c4.metric("p95, с", m["p95"])
        else:
            st.caption("Статистика появится после первого хода.")
        st.text_input("Адрес API", value=DEFAULT_API_URL, key="api_url")


# ---------------------------------------------------------------- отрисовка
@st.dialog("Выбор задачи", width="large", on_dismiss=close_task_picker)
def task_dialog(student_id: str, bank: list[dict], solved: list, nonce: int) -> None:
    query = st.text_input("Найти задачу", placeholder="Название функции, текст условия или ID", key="task_query")
    label = st.radio("Показывать", list(STATUS_FILTERS), horizontal=True, key="task_filter")
    rows = task_rows(bank, solved, query, STATUS_FILTERS[label])
    st.caption(f"Найдено {len(rows)} из {len(bank)} · Выберите задачу слева, чтобы прочитать условие.")
    if not rows:
        st.info("По вашему запросу задач нет. Попробуйте другое слово или переключите фильтр на «Все».")
        return
    by_id = {t["task_id"]: t for t in bank}
    ids = [row["task_id"] for row in rows]
    current = (state.last.get(student_id, {}).get("task") or {}).get("task_id")
    left, right = st.columns([2, 3], gap="large")
    with left.container(height=360, border=True):
        selected = st.radio("Задачи", ids, index=ids.index(current) if current in ids else 0,
                            format_func=lambda tid: task_option_label(by_id[tid], solved, width=42),
                            key=f"task_choice_{student_id}_{nonce}", label_visibility="collapsed")
    with right:
        task = by_id[selected]
        parts = parse_statement(task.get("statement", ""))
        st.markdown(f"#### {task.get('entry_point') or parts['entry_point'] or selected}")
        tags = ["Решена" if selected in solved else "Ещё не решена"]
        if selected == current:
            tags.append("Текущая задача")
        draft = state.drafts.get(student_id, {}).get(selected)
        if draft is not None and draft != task.get("starter", ""):
            tags.append("Есть черновик")
        if task.get("difficulty"):
            tags.append(f"Сложность: {task['difficulty']}")
        st.caption(" · ".join(tags))
        st.write(parts["description"])
        render_task_example(task)
        if task.get("starter"):
            with st.expander("Заготовка функции"):
                st.code(task["starter"], language="python")
        button = "Вернуться к решению" if selected == current else ("Продолжить решение" if draft is not None else "Начать решение")
        if st.button(button, type="primary", key="confirm_task", use_container_width=True):
            if selected == current or take_task(student_id, selected, task.get("starter", "")):
                close_task_picker()
                st.rerun()
            if error := state.pop("flash", None):
                st.error(error)
        st.caption("При смене задачи ваш текущий черновик сохранится.")


def render_agent_details(d: dict) -> None:
    with st.expander("Что делали агенты"):
        tab_check, tab_time, tab_tech = st.tabs(["Проверка", "Время", "Служебное"])
        with tab_check:
            v = verdict_info(d.get("verdict"))
            if v:
                st.markdown(f"**Вердикт проверяющего:** {v['icon']} {v['text']}")
                if d["verdict"].get("error_type"):
                    st.caption(f"Тип ошибки: {d['verdict']['error_type']}")
                if d["verdict"].get("failed_tests"):
                    st.code("\n".join(d["verdict"]["failed_tests"]), language="python")
            a = d.get("analysis")
            if a:
                st.markdown(f"**Разбор ошибки:** {category_label(a['category'])}. {a['summary']}")
                st.caption(f"Наводка для тьютора: {a['hint_for_tutor']}")
        with tab_time:
            rows = timings_table(d.get("timings"))
            if rows:
                st.caption(pipeline_line(d["timings"]))
                st.dataframe(
                    pd.DataFrame(rows), hide_index=True,
                    column_config={"Доля, %": st.column_config.ProgressColumn(
                        "Доля, %", min_value=0, max_value=100, format="%d%%")},
                )
                st.caption(f"Всего {d.get('total_seconds', 0)} с; дольше всего: {slowest_step(d['timings'])}")
        with tab_tech:
            st.write(f"Статус: {status_label(d.get('status'))}; повторов запроса к агентам: {d.get('retries', 0)}")
            if d.get("errors"):
                st.error("\n".join(d["errors"]))


def render_message(item: dict) -> None:
    with st.chat_message(item["role"], avatar=AVATARS[item["role"]]):
        if item.get("service_error"):
            st.warning(item["text"])
        else:
            st.write(item["text"])
        if item.get("code"):
            st.code(item["code"], language="python")
        d = item.get("details")
        if not d:
            return
        badges = []
        v = verdict_info(d.get("verdict"))
        if v:
            badges.append(f":{v['color']}[{v['icon']} {v['text']}]")
        if not (v and v["kind"] == "pass"):   # после успеха уровень подсказки не показываем
            badges.append(hint_text(d.get("hint_level", 1)))
        if d.get("status") != "ok":
            badges.append(f":red[{status_label(d.get('status'))}]")
        if d.get("total_seconds") is not None:
            badges.append(f"{d['total_seconds']:.1f} с")
        st.caption(" · ".join(badges))


def ask(message: str, code: str | None) -> dict:
    """Один ход со шкалой прогресса: шаги графа появляются по мере выполнения.
    У старого бэкенда без /chat/stream работает обычный /chat."""
    with st.status("Репетитор работает…", expanded=dev_mode) as status:
        data = None
        try:
            try:
                for ev in client.chat_stream(student_id, message, code):
                    if ev["type"] == "step":
                        status.write(step_line(ev))
                    elif ev["type"] == "result":
                        data = ev["data"]
                    elif ev["type"] == "error":
                        raise ApiError(ev.get("message", "Ошибка на стороне сервера."))
            except ApiError as e:
                if e.status != 404:
                    raise
                data = client.chat(student_id, message, code)   # старый бэкенд
            if data is None:
                raise ApiError("Сервер оборвал ответ. Попробуйте ещё раз.")
        except ApiError:
            status.update(label="Не получилось", state="error")
            raise
        status.update(label=f"Готово за {data['total_seconds']:.1f} с", state="complete", expanded=False)
    return data


def send(message: str, code: str | None = None, retry: bool = False) -> None:
    """Отправляет ход в API: сразу показывает вопрос студента, ждёт ответ и обновляет страницу."""
    state.failed_requests.pop(student_id, None)
    if retry and chat and chat[-1].get("service_error"):
        chat.pop()
    else:
        chat.append({"role": "user", "text": message, "code": code})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.write(message)
        if code:
            st.code(code, language="python")
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        try:
            data = ask(message, code)
            incomplete = data.get("status") != "ok"
            chat.append({"role": "assistant", "text": data["reply"], "details": data,
                         "service_error": incomplete})
            state.last[student_id] = data
            state.solved[student_id] = data.get("solved", solved)
            result_task_id = (data.get("task") or {}).get("task_id", task_id_now)
            if data.get("verdict") is not None:
                state.results.setdefault(student_id, {})[result_task_id] = {"data": data, "code": code}
            elif incomplete:
                state.failed_requests[student_id] = {"message": message, "code": code,
                    "task_id": result_task_id, "error": turn_error_text(data), "details": data.get("errors", [])}
        except ApiError as e:
            state.failed_requests[student_id] = {"message": message, "code": code,
                                                "task_id": task_id_now, "error": e.message}
            chat.append({"role": "assistant", "text": e.message, "service_error": True})
    st.rerun()


def render_task_panel() -> None:
    with st.container(border=True):
        title, actions = st.columns([3, 2], vertical_alignment="center")
        with actions:
            if bank:
                c1, c2 = st.columns(2)
                c1.button("Список задач", key="open_tasks", on_click=open_task_picker)
                nxt = next_unsolved(bank, solved, task_id_now)
                c2.button("Следующая задача →", key="next_task", disabled=nxt is None,
                          help="Все задачи решены." if nxt is None else None,
                          on_click=take_task, args=(student_id, nxt or "", starter_for(bank, nxt)))
        if task_now:
            parts = parse_statement(task_now["statement"])
            done = task_id_now in solved
            bank_task = next((t for t in bank if t["task_id"] == task_id_now), {})
            name = parts["entry_point"] or bank_task.get("entry_point") or task_id_now.rsplit("/", 1)[-1]
            title.markdown(f"#### {name}" + ("  :green[Решено ✅]" if done else ""))
            st.write(parts["description"])
            render_task_example(bank_task or task_now)
            v = verdict_info((last or {}).get("verdict"))
            if last and not done and not (v and v["kind"] == "pass"):
                level = clamp_hint(last.get("hint_level", 1))
                st.progress(level / MAX_HINT, text=hint_text(level))
        else:
            title.markdown("#### Выберите задачу")
            st.caption("Откройте список задач или просто напишите репетитору: он выдаст задачу сам."
                       if bank else "Напишите репетитору, например «Не понимаю задачу»: он выдаст задачу.")


def render_chat() -> str | None:
    """Сообщения диалога; для пустого чата приветствие и кнопки-подсказки. Возвращает нажатую кнопку."""
    quick = None
    if not chat:
        with st.chat_message("assistant", avatar=AVATARS["assistant"]):
            st.write("Задача выбрана. Напишите решение справа или спросите меня, с чего начать.")
        cols = st.columns(len(EXAMPLE_PROMPTS))
        for col, prompt in zip(cols, EXAMPLE_PROMPTS):
            if col.button(prompt, key=f"quick_{prompt}", disabled=not api_ok):
                quick = prompt
    for item in chat:
        render_message(item)
    return quick


def render_result(code: str) -> bool:
    with st.container(border=True):
        failed = state.failed_requests.get(student_id)
        if failed and failed["task_id"] == task_id_now:
            st.warning("Не удалось выполнить проверку" if failed.get("code") else "Не удалось получить ответ")
            st.write(failed["error"])
            if failed.get("details"):
                with st.expander("Подробности ошибки"):
                    st.code("\n".join(failed["details"]), language="text")
            st.caption("Код в редакторе сохранён. Повтор отправит исходный запрос.")
            return st.button("Повторить запрос", key="retry_request", disabled=not api_ok)
        record = state.results.get(student_id, {}).get(task_id_now)
        data = record["data"] if record else {}
        v = verdict_info(data.get("verdict"))
        if not v:
            st.caption("Здесь появится результат проверки. Нажмите «Проверить решение».")
            return False
        if record["code"] != code:
            st.info("Код изменён после проверки. Ниже — результат предыдущего варианта.")
        if v["kind"] == "pass":
            st.success("✅ Все тесты прошли")
        else:
            st.error("❌ Тесты не прошли")
            detail = failed_test_details(data["verdict"])
            if detail:
                st.caption("Первый проваленный тест")
                if detail["call"]:
                    st.code(detail["call"], language="python")
                    c1, c2 = st.columns(2)
                    c1.caption("Ожидалось")
                    c1.code(detail["expected"], language="python")
                    c2.caption("Получено")
                    c2.code(detail["got"] if detail["got"] is not None else "Результат не получен", language="python")
                with st.expander("Подробности теста"):
                    st.code(detail["test"], language="python")
                    if not detail["call"] and detail["got"] is not None:
                        st.text(f"Получено: {detail['got']}")
                    if data["verdict"].get("traceback_short"):
                        st.text(data["verdict"]["traceback_short"])
            if data["verdict"].get("error_type") not in (None, "AssertionError"):
                st.caption(f"Ошибка выполнения: {data['verdict']['error_type']}")
            a = data.get("analysis")
            if a:
                st.markdown(f"**Что не так:** {category_label(a['category'])}. {a['summary']}")
        st.caption(f"{hint_text(data.get('hint_level', 1)) if v['kind'] == 'fail' else 'Задача решена'}"
                   f" · {data.get('total_seconds', 0):.1f} с")
        if data.get("status") != "ok":
            st.warning("Тесты выполнены, но сервис не смог завершить ответ репетитора.")
        elif any(str(e).startswith("review:") for e in data.get("errors", [])):
            st.warning("Тесты выполнены, но разбор ошибки от модели недоступен.")
        if data.get("errors"):
            with st.expander("Подробности ошибки сервиса"):
                st.code("\n".join(data["errors"]), language="text")
        if dev_mode:
            render_agent_details(data)
    return False


def render_start_screen() -> tuple | None:
    with st.container(border=True, key="start_screen"):
        with st.chat_message("assistant", avatar=AVATARS["assistant"]):
            st.markdown("### Привет! Давайте разберём задачу по Python")
            st.write("Я помогу с ходом решения, объясню ошибки и дам подсказки. Начните с выбора задачи — затем откроются редактор кода и чат.")
        if bank:
            st.caption(f"Задач в банке: {len(bank)} · Решено: {len(set(solved))}")
            choose, suggested = st.columns(2)
            choose.button("Выбрать задачу", type="primary", key="open_tasks_start", use_container_width=True,
                          on_click=open_task_picker)
            first = next_unsolved(bank, solved)
            if first:
                suggested.button("Начать с первой нерешённой", key="start_first", use_container_width=True,
                                 on_click=take_task, args=(student_id, first, starter_for(bank, first)))
            else:
                st.success("Все задачи решены. Выберите любую задачу, чтобы повторить её.")
        else:
            st.caption("Репетитор предложит задачу, с которой можно начать практику.")
            if st.button("Получить задачу", type="primary", key="get_first_task", disabled=not api_ok):
                return ("Предложи задачу по Python", None)
    return None


# ---------------------------------------------------------------- страница
st.title("Репетитор по Python")
st.caption("Подсказки вместо готового кода. Решение проверяется запуском тестов.")

if flash := state.pop("flash", None):
    st.error(flash)
if not api_ok:
    st.error("Сервис не отвечает, поэтому чат отключён. Запустите бэкенд (команда в боковой панели) "
             "и нажмите «Проверить связь».")

pending = None
if task_now:
    render_task_panel()
    with st.container(key="workspace"):
        left, right = st.columns([5, 6], gap="large")

    with left:
        st.markdown("#### Обсуждение")
        chat_box = st.container(height=360, border=True)
        with chat_box:
            quick = render_chat()
        typed = st.chat_input("Спросите репетитора, например: «Дай подсказку»", disabled=not api_ok)
        hint_clicked = st.button("Дай подсказку", key="hint", disabled=not api_ok or not task_id_now,
                                help="Попросить подсказку по текущей задаче.")

    with right:
        st.markdown("#### Решение")
        if f"code_{student_id}_{ed['ver']}" not in state:
            ed["seed"] = state.drafts.get(student_id, {}).get(task_id_now, ed["seed"])
        code = code_editor(f"code_{student_id}_{ed['ver']}", height=360, value=ed["seed"])
        state.drafts.setdefault(student_id, {})[task_id_now] = code
        b1, b2 = st.columns([3, 2])
        run_clicked = b1.button("▶ Проверить решение", type="primary", key="run",
                                disabled=not api_ok or not code.strip())
        with b2.popover("Сбросить код", disabled=not task_id_now):
            st.write("Заменить текущий черновик заготовкой функции?")
            st.button("Да, сбросить код", key="reset_code", on_click=reset_editor,
                      args=(student_id, starter_for(bank, task_id_now), task_id_now))
        st.caption("Черновик сохраняется при смене задачи в этой сессии браузера.")
        retry_clicked = render_result(code)

        attempts = attempts_table(chat)
        if attempts:
            with st.expander(f"Мои попытки ({len(attempts)})"):
                st.dataframe(pd.DataFrame(attempts), hide_index=True)

    if run_clicked:
        pending = (CHECK_CODE_MESSAGE, code)
    elif typed:
        pending = (typed, None)
    elif quick:
        pending = (quick, None)
    elif hint_clicked:
        pending = ("Дай подсказку по текущей задаче", None)
    elif retry_clicked:
        failed = state.failed_requests[student_id]
        pending = (failed["message"], failed["code"], True)
else:
    pending = render_start_screen()
    chat_box = st.container()
    if chat:
        with st.expander("Предыдущий диалог"):
            for item in chat:
                render_message(item)
    if state.failed_requests.get(student_id, {}).get("task_id", "") is None:
        if render_result(""):
            failed = state.failed_requests[student_id]
            pending = (failed["message"], failed["code"], True)

if state.get("task_picker_open") and bank:
    task_dialog(student_id, bank, solved, state["dlg"])

if pending:
    with chat_box:
        send(*pending)
