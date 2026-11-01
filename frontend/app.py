"""Интерфейс чата (Streamlit).

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
from frontend.style import apply_style
from frontend.helpers import (
    CHECK_CODE_MESSAGE,
    EXAMPLE_PROMPTS,
    MAX_HINT,
    category_label,
    chat_to_markdown,
    clamp_hint,
    hint_text,
    history_to_chat,
    pipeline_line,
    slowest_step,
    status_label,
    timings_table,
    verdict_info,
)

DEFAULT_API_URL = os.getenv("TUTOR_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Репетитор по Python", page_icon="🐍", layout="wide")
apply_style()

state = st.session_state
state.setdefault("chats", {})   # student_id -> сообщения чата
state.setdefault("last", {})    # student_id -> последний ответ API (задача, вердикт, подсказка)


@st.cache_data(ttl=3, show_spinner=False)
def api_status(url: str):
    """(жив ли API, режим работы). Кэш на 3 секунды, чтобы не дёргать API на каждый клик."""
    c = TutorClient(url)
    ok = c.health()
    return ok, (c.info() if ok else None)


# ---------------------------------------------------------------- боковая панель
api_url = state.get("api_url", DEFAULT_API_URL)
api_ok, info = api_status(api_url)
client = TutorClient(api_url)

with st.sidebar:
    st.title("🐍 Репетитор")
    if api_ok:
        st.markdown(":green[● API на связи]")
        if info:
            checker = "настоящий" if info.get("checker") == "real" else "заглушка"
            tutor = "GigaChat" if info.get("tutor") == "llm" else "заглушка"
            st.caption(f"Проверяющий: {checker} · Тьютор: {tutor}")
    else:
        st.markdown(":red[● API недоступен]")
        st.caption("Запустите бэкенд в соседнем терминале:")
        st.code("uvicorn api.main:app --reload", language="bash")
        if st.button("Проверить связь"):
            api_status.clear()
            st.rerun()

    student_id = st.text_input("ID студента", value="s1", key="student_id").strip() or "s1"
    dev_mode = st.toggle("Показывать работу агентов", value=True, key="dev_mode",
                         help="Под каждым ответом: задача, вердикт, разбор ошибки и время шагов.")

    if st.button("Новая сессия", disabled=not api_ok,
                 help="Сбросить диалог, задачу и уровень подсказки этого студента."):
        try:
            client.reset(student_id)
        except ApiError as e:
            st.error(e.message)
        state.chats.pop(student_id, None)
        state.last.pop(student_id, None)
        state.pop(f"code_{student_id}", None)

    current_chat = state.chats.get(student_id, [])
    st.download_button("Скачать диалог (.md)", data=chat_to_markdown(current_chat, student_id),
                       file_name=f"dialog_{student_id}.md", mime="text/markdown",
                       disabled=not current_chat)

    with st.expander("Статистика оркестратора"):
        m = client.metrics() if api_ok else None
        if not m or not m.get("sessions"):
            st.caption("Пока нет данных: отправьте сообщение в чат.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("Ходов", m["sessions"])
            c2.metric("Ошибок", f"{round(m['error_rate'] * 100)}%")
            c3, c4 = st.columns(2)
            c3.metric("p50, с", m["p50"])
            c4.metric("p95, с", m["p95"])

    with st.expander("Настройки"):
        st.text_input("Адрес API", value=DEFAULT_API_URL, key="api_url")


# ---------------------------------------------------------------- состояние студента
if api_ok and student_id not in state.chats:
    try:  # после обновления страницы подтягиваем историю с сервера
        sess = client.session(student_id)
        state.chats[student_id] = history_to_chat(sess.get("history"))
        if sess.get("task"):
            state.last[student_id] = {"task": sess["task"], "hint_level": sess.get("hint_level", 1)}
    except ApiError:
        pass
chat = state.chats.setdefault(student_id, [])
last = state.last.get(student_id)


# ---------------------------------------------------------------- отрисовка
def render_task_card(last: dict | None) -> None:
    with st.container(border=True):
        st.subheader("Задача")
        task = (last or {}).get("task")
        if task:
            st.write(task["statement"])
            st.caption(f"Тема: {task['topic']}")
        else:
            st.write("Задача появится после первого сообщения. "
                     "Напишите в чат, например, «Не понимаю задачу».")
        v = verdict_info((last or {}).get("verdict"))
        if v:
            st.markdown(f":{v['color']}[{v['icon']} {v['text']}]")
            if v["kind"] == "pass":
                st.caption("Задача решена. Новая выдастся со следующим сообщением.")
        if last and not (v and v["kind"] == "pass"):
            level = clamp_hint(last.get("hint_level", 1))
            st.progress(level / MAX_HINT, text=hint_text(level))


def render_agent_details(d: dict) -> None:
    with st.expander("Что делали агенты"):
        tab_check, tab_time, tab_tech = st.tabs(["Проверка", "Время", "Служебное"])
        with tab_check:
            if d.get("task"):
                st.markdown(f"**Задача:** {d['task']['statement']}")
            v = verdict_info(d.get("verdict"))
            if v:
                st.markdown(f"**Вердикт проверяющего:** {v['icon']} {v['text']}")
                if d["verdict"].get("error_type"):
                    st.caption(f"Тип ошибки: {d['verdict']['error_type']}")
                if d["verdict"].get("failed_tests"):
                    st.code("\n".join(d["verdict"]["failed_tests"]), language="python")
            else:
                st.caption("Код не присылали, проверка не запускалась.")
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
    with st.chat_message(item["role"]):
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
        if dev_mode:
            render_agent_details(d)


def send(message: str, code: str | None = None) -> None:
    """Отправляет ход в API: сразу показывает вопрос студента, ждёт ответ и обновляет страницу."""
    chat.append({"role": "user", "text": message, "code": code})
    with st.chat_message("user"):
        st.write(message)
        if code:
            st.code(code, language="python")
    with st.chat_message("assistant"):
        with st.spinner("Репетитор думает…"):
            try:
                data = client.chat(student_id, message, code)
                chat.append({"role": "assistant", "text": data["reply"], "details": data})
                state.last[student_id] = data
            except ApiError as e:
                chat.append({"role": "assistant", "text": e.message})
    st.rerun()


# ---------------------------------------------------------------- страница
st.title("Репетитор по Python")
st.caption("Подсказки вместо готового кода. Решение проверяется запуском тестов.")

if not api_ok:
    st.error("Бэкенд не отвечает, поэтому чат отключён. Запустите его (команда в боковой панели) "
             "и нажмите «Проверить связь».")

left, right = st.columns(2)
with left:
    render_task_card(last)
with right:
    with st.container(border=True):
        st.subheader("Мой код")
        code = code_editor(f"code_{student_id}")
        check_clicked = st.button("Проверить код", type="primary",
                                  disabled=not api_ok or not code.strip())

for item in chat:
    render_message(item)

if not chat and api_ok:
    with st.container(border=True):
        st.markdown("**Как это работает**")
        st.markdown(
            "1. Напишите в чат, что непонятно, или нажмите на пример ниже: репетитор выдаст задачу.\n"
            "2. Напишите решение в поле «Мой код» и нажмите «Проверить код».\n"
            "3. Тесты покажут, что не так. Готового ответа репетитор не даст, только наводящие вопросы."
        )
    cols = st.columns(len(EXAMPLE_PROMPTS))
    for col, prompt in zip(cols, EXAMPLE_PROMPTS):
        if col.button(prompt):
            send(prompt)

if check_clicked:
    send(CHECK_CODE_MESSAGE, code)

if prompt := st.chat_input("Напишите сообщение репетитору", disabled=not api_ok):
    send(prompt)