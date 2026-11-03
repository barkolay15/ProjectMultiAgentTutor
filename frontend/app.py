"""Интерфейс репетитора (Streamlit): слева условие и чат, справа редактор кода и результат проверки.

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
    task_rows,
    timings_table,
    verdict_info,
)
from frontend.style import apply_style

DEFAULT_API_URL = os.getenv("TUTOR_API_URL", "http://127.0.0.1:8000")
AVATARS = {"user": "🧑‍💻", "assistant": "🧑‍🏫"}

st.set_page_config(page_title="Репетитор по Python", page_icon="🐍", layout="wide")
apply_style()

state = st.session_state
state.setdefault("chats", {})   # student_id -> сообщения чата
state.setdefault("last", {})    # student_id -> последний ответ API (задача, вердикт, подсказка)
state.setdefault("solved", {})  # student_id -> id решённых задач
state.setdefault("editor", {})  # student_id -> {"ver": номер поля, "seed": начальный код, "task_id": задача}


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


def take_task(student_id: str, task_id: str, starter: str = "") -> None:
    """Студент выбрал задачу: сервер запоминает её, в редакторе появляется заготовка функции.
    Работает и как on_click (до перерисовки страницы), поэтому берёт всё из аргументов и состояния."""
    try:
        data = TutorClient(state.get("api_url", DEFAULT_API_URL)).set_task(student_id, task_id)
    except ApiError as e:
        state["flash"] = e.message
        return
    state.last[student_id] = {"task": data["task"], "hint_level": 1}
    reset_editor(student_id, data.get("starter") or starter, task_id)


# ---------------------------------------------------------------- боковая панель, часть 1
api_url = state.get("api_url", DEFAULT_API_URL)
api_ok, info = api_status(api_url)
client = TutorClient(api_url)

with st.sidebar:
    st.title("🐍 Репетитор")
    student_id = st.text_input("Ваше имя", value="s1", key="student_id",
                               help="Под этим именем сохраняются ваш диалог и прогресс.").strip() or "s1"
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

# в редакторе всегда заготовка текущей задачи (если задачу выдал сам репетитор, подставляем её здесь)
ed = state.editor.setdefault(student_id, {"ver": 0, "seed": "", "task_id": None})
if task_id_now and ed.get("task_id") != task_id_now:
    reset_editor(student_id, starter_for(bank, task_id_now), task_id_now)
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
            state.chats.pop(student_id, None)
            state.last.pop(student_id, None)
            state.solved.pop(student_id, None)
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
@st.dialog("Список задач", width="large")
def task_dialog(student_id: str, bank: list[dict], solved: list, nonce: int) -> None:
    left, right = st.columns([3, 2])
    query = left.text_input("Поиск", placeholder="Название функции или слово из описания", key="task_query")
    label = right.radio("Показывать", list(STATUS_FILTERS), horizontal=True, key="task_filter")
    rows = task_rows(bank, solved, query, STATUS_FILTERS[label])
    if not rows:
        st.info("Ничего не найдено. Измените поиск или фильтр.")
        return
    with_difficulty = any(r["difficulty"] for r in rows)
    table = pd.DataFrame([
        {"": r["mark"], "Задача": r["name"], **({"Сложность": r["difficulty"]} if with_difficulty else {}),
         "Описание": r["description"]} for r in rows])
    event = st.dataframe(table, hide_index=True, height=380, on_select="rerun",
                         selection_mode="single-row", key=f"task_table_{nonce}")
    st.caption("Нажмите на строку, чтобы взять задачу.")
    picked = event.selection.rows
    if picked:
        task_id = rows[picked[0]]["task_id"]
        take_task(student_id, task_id, starter_for(bank, task_id))
        st.rerun()


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


def send(message: str, code: str | None = None) -> None:
    """Отправляет ход в API: сразу показывает вопрос студента, ждёт ответ и обновляет страницу."""
    chat.append({"role": "user", "text": message, "code": code})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.write(message)
        if code:
            st.code(code, language="python")
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        try:
            data = ask(message, code)
            chat.append({"role": "assistant", "text": data["reply"], "details": data})
            state.last[student_id] = data
            state.solved[student_id] = data.get("solved", solved)
        except ApiError as e:
            st.error(e.message)
            chat.append({"role": "assistant", "text": e.message})
    st.rerun()


def render_task_panel() -> None:
    with st.container(border=True):
        if task_now:
            parts = parse_statement(task_now["statement"])
            done = task_id_now in solved
            st.markdown(f"#### {parts['entry_point'] or task_id_now}" + ("  :green[Решено ✅]" if done else ""))
            st.write(parts["description"])
            if parts["example"]:
                st.caption("Пример проверки")
                st.code(parts["example"], language="python")
            v = verdict_info((last or {}).get("verdict"))
            if last and not done and not (v and v["kind"] == "pass"):
                level = clamp_hint(last.get("hint_level", 1))
                st.progress(level / MAX_HINT, text=hint_text(level))
        else:
            st.markdown("#### Выберите задачу")
            st.caption("Откройте список задач или просто напишите репетитору: он выдаст задачу сам."
                       if bank else "Напишите репетитору, например «Не понимаю задачу»: он выдаст задачу.")
        if bank:
            c1, c2 = st.columns(2)
            if c1.button("Список задач", key="open_tasks"):
                state["dlg"] = state.get("dlg", 0) + 1
                task_dialog(student_id, bank, solved, state["dlg"])
            nxt = next_unsolved(bank, solved, task_id_now)
            c2.button("Следующая задача →", key="next_task", disabled=nxt is None,
                      help="Все задачи решены." if nxt is None else None,
                      on_click=take_task, args=(student_id, nxt or "", starter_for(bank, nxt)))


def render_chat() -> str | None:
    """Сообщения диалога; для пустого чата приветствие и кнопки-подсказки. Возвращает нажатую кнопку."""
    quick = None
    if not chat:
        st.markdown("**Привет! Я помогу разобраться с задачей, но готовый код не дам.**")
        st.caption("Спросите, с чего начать, или попросите подсказку.")
        cols = st.columns(len(EXAMPLE_PROMPTS))
        for col, prompt in zip(cols, EXAMPLE_PROMPTS):
            if col.button(prompt, key=f"quick_{prompt}", disabled=not api_ok):
                quick = prompt
    for item in chat:
        render_message(item)
    return quick


def render_result() -> None:
    with st.container(border=True):
        v = verdict_info((last or {}).get("verdict"))
        if not v:
            st.caption("Здесь появится результат проверки. Нажмите «Проверить решение».")
            return
        if v["kind"] == "pass":
            st.success("✅ Все тесты прошли")
        else:
            st.error("❌ Тесты не прошли")
            failed = last["verdict"].get("failed_tests") or []
            if failed:
                st.caption("Первый проваленный тест")
                st.code(failed[0], language="python")
            a = last.get("analysis")
            if a:
                st.markdown(f"**Что не так:** {category_label(a['category'])}. {a['summary']}")
        st.caption(f"{hint_text(last.get('hint_level', 1)) if v['kind'] == 'fail' else 'Задача решена'}"
                   f" · {last.get('total_seconds', 0):.1f} с")
        if dev_mode:
            render_agent_details(last)


# ---------------------------------------------------------------- страница
st.title("Репетитор по Python")
st.caption("Подсказки вместо готового кода. Решение проверяется запуском тестов.")

if flash := state.pop("flash", None):
    st.error(flash)
if not api_ok:
    st.error("Сервис не отвечает, поэтому чат отключён. Запустите бэкенд (команда в боковой панели) "
             "и нажмите «Проверить связь».")

left, right = st.columns([5, 6], gap="large")

with left:
    render_task_panel()
    chat_box = st.container(height=440, border=True)
    with chat_box:
        quick = render_chat()
    typed = st.chat_input("Спросите репетитора, например: «Дай подсказку»", disabled=not api_ok)

with right:
    st.markdown("#### Решение")
    code = code_editor(f"code_{student_id}_{ed['ver']}", height=340, value=ed["seed"])
    b1, b2 = st.columns([3, 2])
    run_clicked = b1.button("▶ Проверить решение", type="primary", key="run",
                            disabled=not api_ok or not code.strip())
    b2.button("Сбросить код", key="reset_code", disabled=not task_id_now,
              help="Вернуть заготовку функции", on_click=reset_editor,
              args=(student_id, starter_for(bank, task_id_now), task_id_now))
    render_result()

    attempts = attempts_table(chat)
    if attempts:
        with st.expander(f"Мои попытки ({len(attempts)})"):
            st.dataframe(pd.DataFrame(attempts), hide_index=True)

pending = None
if run_clicked:
    pending = (CHECK_CODE_MESSAGE, code)
elif typed:
    pending = (typed, None)
elif quick:
    pending = (quick, None)
if pending:
    with chat_box:
        send(*pending)
