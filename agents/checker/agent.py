"""Агент D «Проверяющий» целиком: LLM + инструмент run_tests + проверка ответа кодом.

Цикл (как в LangGraph-узле, только без графа):
  1. модель получает запрос и сама решает вызвать инструмент run_tests;
  2. код запускает тесты (вердикт получен запуском, а не мнением модели);
  3. модель разбирает ошибку в ErrorAnalysis (JSON);
  4. код проверяет JSON по схеме и что в нём нет готовой программы;
     при ошибке просит модель повторить (не более MAX_RETRIES раз).

analyze(request, llm) -> (CheckVerdict, ErrorAnalysis | None)
Вердикт возвращается всегда: он не зависит от модели.
"""
import re

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agents.checker.sandbox import check
from agents.checker.prompts import CHECKER_SYSTEM_PROMPT
from schemas.messages import CheckRequest, CheckVerdict, ErrorAnalysis, parse_reply

MAX_STEPS = 4      # лимит шагов цикла (защита от бесконечных вызовов)
MAX_RETRIES = 2    # сколько раз просим модель исправить невалидный ответ


@tool
def run_tests(task_id: str, student_code: str) -> str:
    """Запускает код студента на тестах задачи, возвращает вердикт в JSON."""
    return check(CheckRequest(task_id=task_id, student_code=student_code)).model_dump_json()


_CODE_LINE = re.compile(r"^\s*(def |return\b|for .+ in |while .+:|import |from .+ import |class )", re.M)


def looks_like_solution(text: str) -> bool:
    """Эвристика: в тексте есть блок кода или строки программы."""
    return "```" in text or bool(_CODE_LINE.search(text))


def _analysis_problem(analysis: ErrorAnalysis, task_id: str) -> str | None:
    if analysis.task_id != task_id:
        return f"task_id должен быть {task_id}"
    if looks_like_solution(analysis.summary) or looks_like_solution(analysis.hint_for_tutor):
        return "в тексте есть код; опиши ошибку словами, без программы"
    return None


def analyze(request: CheckRequest, llm) -> tuple[CheckVerdict, ErrorAnalysis | None]:
    model = llm.bind_tools([run_tests])
    messages = [
        SystemMessage(content=CHECKER_SYSTEM_PROMPT),
        HumanMessage(content=request.model_dump_json()),
    ]
    verdict: CheckVerdict | None = None
    retries = 0

    for _ in range(MAX_STEPS):
        ai = model.invoke(messages)
        messages.append(ai)

        if ai.tool_calls:
            for call in ai.tool_calls:
                if call["name"] != "run_tests":
                    messages.append(ToolMessage(content='{"error": "неизвестный инструмент"}',
                                                tool_call_id=call["id"]))
                    continue
                # Аргументы берём из запроса, а не из ответа модели:
                # модель решает КОГДА вызвать, но не подменяет код студента.
                result = run_tests.invoke({"task_id": request.task_id,
                                           "student_code": request.student_code})
                verdict = CheckVerdict.model_validate_json(result)
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
            if verdict is not None and verdict.passed:
                return verdict, None        # тесты зелёные, разбирать нечего
            continue

        if verdict is None:                  # модель ответила, не вызвав инструмент
            verdict = check(request)
            if verdict.passed:
                return verdict, None
            messages.append(HumanMessage(
                content="Сначала запусти тесты инструментом run_tests, вот вердикт: "
                        + verdict.model_dump_json()))
            continue

        analysis, err = parse_reply(ErrorAnalysis, ai.content)
        if analysis is not None:
            err = _analysis_problem(analysis, request.task_id)
        if err is None:
            return verdict, analysis
        if retries >= MAX_RETRIES:
            break
        retries += 1
        messages.append(HumanMessage(
            content=f"Ответ не принят: {err}. Верни один JSON-объект по схеме."))

    return verdict or check(request), None
