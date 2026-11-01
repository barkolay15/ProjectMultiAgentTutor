"""Интерфейс чата (Streamlit). Запуск: streamlit run frontend/app.py
Бэкенд должен работать: uvicorn api.main:app --reload
"""
import requests
import streamlit as st

st.set_page_config(page_title="Репетитор по Python", page_icon="🐍")
st.title("Репетитор по Python")

api_url = st.sidebar.text_input("Адрес API", "http://127.0.0.1:8000")
student_id = st.sidebar.text_input("ID студента", "s1")
if st.sidebar.button("Сбросить сессию"):
    requests.post(f"{api_url}/reset/{student_id}", timeout=10)
    st.session_state.pop("chat", None)
    st.rerun()

chat = st.session_state.setdefault("chat", [])
for item in chat:
    with st.chat_message(item["role"]):
        st.write(item["text"])
        if item.get("details"):
            d = item["details"]
            with st.expander("Что делали агенты"):
                if d.get("task"):
                    st.markdown(f"**Задача:** {d['task']['statement']}")
                if d.get("verdict"):
                    st.markdown("**Вердикт проверяющего:** " + ("тесты прошли" if d["verdict"]["passed"] else "тесты не прошли"))
                    if d["verdict"].get("failed_tests"):
                        st.code("\n".join(d["verdict"]["failed_tests"]))
                if d.get("analysis"):
                    st.markdown(f"**Разбор ошибки:** {d['analysis']['category']}: {d['analysis']['summary']}")
                st.caption(f"Статус: {d['status']} · повторов: {d['retries']} · {d['total_seconds']} с")
                if d.get("errors"):
                    st.error("\n".join(d["errors"]))
                st.dataframe(d["timings"], hide_index=True)

code = st.sidebar.text_area("Ваш код (необязательно)", height=200)
message = st.chat_input("Напишите сообщение")
if message:
    chat.append({"role": "user", "text": message})
    try:
        r = requests.post(
            f"{api_url}/chat",
            json={"student_id": student_id, "message": message, "code": code or None},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        chat.append({"role": "assistant", "text": data["reply"], "details": data})
    except requests.RequestException as e:
        chat.append({"role": "assistant", "text": f"Не удалось связаться с API: {e}"})
    st.rerun()