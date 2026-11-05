"""Запросы интерфейса сохраняют привязку к отображаемой задаче."""
from unittest.mock import Mock

from frontend.client import TutorClient


def test_chat_requests_include_displayed_task_id():
    client = TutorClient("http://test.invalid")
    response = Mock(status_code=200)
    response.json.return_value = {"reply": "ok"}
    response.iter_lines.return_value = [b'{"type": "result", "data": {"reply": "ok"}}']
    client._http = Mock()
    client._http.request.return_value = response
    client._http.post.return_value = response
    assert client.chat("s1", "check", "draft", task_id="task/one")["reply"] == "ok"
    assert list(client.chat_stream("s1", "check", "draft", task_id="task/one"))[-1]["type"] == "result"
    for call in [client._http.request.call_args, client._http.post.call_args]:
        assert call.kwargs["json"] == {"student_id": "s1", "message": "check", "code": "draft", "task_id": "task/one"}
    response.close.assert_called_once()
