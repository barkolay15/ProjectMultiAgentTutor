"""Живая проверка агента на настоящей модели GigaChat: python3 -m agents.checker.demo"""
from agents.checker.agent import analyze
from core.llm_client import get_llm
from schemas.messages import CheckRequest

CODE = "def sum_even(numbers):\n    return sum(numbers)"   # неверное решение

verdict, analysis = analyze(CheckRequest(task_id="sample/sum_even", student_code=CODE), get_llm())
print("ВЕРДИКТ (запуск тестов):")
print(verdict.model_dump_json(indent=2))
print("\nРАЗБОР (LLM):")
print(analysis.model_dump_json(indent=2) if analysis else "нет: модель не уложилась в схему")
