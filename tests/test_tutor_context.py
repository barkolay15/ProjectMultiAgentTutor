"""Интеграция контекста тьютора: настоящая сборка сообщений, поддельная модель."""
from unittest.mock import Mock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.tutor.agent import run_tutor
from core import llm_client
from schemas.messages import TutorReply, TutorRequest


def test_tutor_sends_history_and_student_code_to_model(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "test-placeholder")
    model = Mock()
    model.invoke.return_value = AIMessage(content=TutorReply(message="Что изменилось?", hint_level=2).model_dump_json())
    monkeypatch.setattr(llm_client, "get_llm", lambda: model)
    old_code = "def strlen(s): return 0"
    new_code = "def strlen(s): return len(s)"
    request = TutorRequest(student_message="Объясни предыдущую подсказку", student_code=new_code, hint_level=2,
                           history=[{"role": "student", "text": "Почему ошибка?", "code": old_code},
                                    {"role": "tutor", "text": "Что вернётся для пустой строки?"}])
    reply = TutorReply.model_validate_json(run_tutor(request))
    assert reply.message == "Что изменилось?"
    messages = model.invoke.call_args.args[0]
    assert len(messages) == 4
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage) and old_code in messages[1].content
    assert isinstance(messages[2], AIMessage) and messages[2].content == request.history[1]["text"]
    assert isinstance(messages[3], HumanMessage)
    assert request.student_message in messages[3].content and new_code in messages[3].content
