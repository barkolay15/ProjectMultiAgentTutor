"""Клиент HTTP API репетитора: все запросы интерфейса идут через этот класс."""
import requests

from frontend.helpers import api_error_text, connection_error_text, parse_stream_line


class ApiError(Exception):
    """Ошибка обращения к API с готовым текстом для студента."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


class TutorClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.strip().rstrip("/")
        self.timeout = timeout
        self._http = requests.Session()

    def _call(self, method: str, path: str, timeout: float | None = None, **kwargs):
        try:
            r = self._http.request(method, f"{self.base_url}{path}",
                                   timeout=timeout or self.timeout, **kwargs)
        except requests.Timeout:
            raise ApiError(connection_error_text("timeout", self.base_url))
        except requests.ConnectionError:
            raise ApiError(connection_error_text("connect", self.base_url))
        except requests.RequestException as e:
            raise ApiError(f"Сетевая ошибка: {e}")
        if r.status_code >= 400:
            detail = None
            try:
                detail = r.json().get("detail")
            except (ValueError, AttributeError):
                pass
            raise ApiError(api_error_text(r.status_code, detail), r.status_code)
        try:
            return r.json()
        except ValueError:
            raise ApiError("Сервер вернул ответ не в формате JSON. Проверьте адрес API.")

    def chat_stream(self, student_id: str, message: str, code: str | None = None):
        """Ход по шагам: отдаёт события step, затем result или error (поток /chat/stream)."""
        try:
            r = self._http.post(f"{self.base_url}/chat/stream", stream=True, timeout=self.timeout,
                                json={"student_id": student_id, "message": message, "code": code or None})
        except requests.Timeout:
            raise ApiError(connection_error_text("timeout", self.base_url))
        except requests.ConnectionError:
            raise ApiError(connection_error_text("connect", self.base_url))
        except requests.RequestException as e:
            raise ApiError(f"Сетевая ошибка: {e}")
        try:
            if r.status_code >= 400:
                detail = None
                try:
                    detail = r.json().get("detail")
                except (ValueError, AttributeError):
                    pass
                raise ApiError(api_error_text(r.status_code, detail), r.status_code)
            for line in r.iter_lines():
                event = parse_stream_line(line)
                if event:
                    yield event
        except requests.Timeout:
            raise ApiError(connection_error_text("timeout", self.base_url))
        except requests.RequestException as e:
            raise ApiError(f"Связь с сервером прервалась: {e}")
        finally:
            r.close()

    def health(self) -> bool:
        try:
            return self._call("GET", "/health", timeout=2).get("status") == "ok"
        except ApiError:
            return False

    def info(self) -> dict | None:
        """Режим работы бэкенда; None у старых версий бэкенда без /info."""
        try:
            return self._call("GET", "/info", timeout=2)
        except ApiError:
            return None

    def metrics(self) -> dict | None:
        try:
            return self._call("GET", "/metrics", timeout=5)
        except ApiError:
            return None

    def session(self, student_id: str) -> dict:
        return self._call("GET", f"/session/{student_id}", timeout=5)

    def tasks(self) -> list[dict]:
        try:
            return self._call("GET", "/tasks", timeout=5)
        except ApiError:
            return []

    def set_task(self, student_id: str, task_id: str) -> dict:
        return self._call("POST", f"/session/{student_id}/task", timeout=5, json={"task_id": task_id})

    def demo(self, task_id: str) -> dict | None:
        """Эталон и неверные решения для показа; None, если демо выключено или данных нет."""
        try:
            return self._call("GET", f"/demo/{task_id}", timeout=5)
        except ApiError:
            return None

    def reset(self, student_id: str) -> None:
        self._call("POST", f"/reset/{student_id}", timeout=5)

    def chat(self, student_id: str, message: str, code: str | None = None) -> dict:
        return self._call("POST", "/chat", json={
            "student_id": student_id, "message": message, "code": code or None})