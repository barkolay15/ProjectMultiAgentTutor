"""Запуск: python3 -m tests.test_schemas  (ключ и сеть не нужны)."""
from schemas.messages import CheckVerdict, MemoryEvent, TutorReply, parse_reply


def test_valid_verdict():
    obj, err = parse_reply(
        CheckVerdict,
        '{"task_id": "humaneval/23", "passed": false, '
        '"failed_tests": ["assert strlen(\'\') == 0"], "error_type": "AssertionError"}',
    )
    assert err is None and obj.passed is False


def test_fenced_json_is_accepted():
    obj, err = parse_reply(MemoryEvent, '```json\n{"student_id":"s1","topic":"циклы","result":"fail"}\n```')
    assert err is None and obj.topic == "циклы"


def test_broken_syntax():
    obj, err = parse_reply(CheckVerdict, '{"task_id": "x", "passed": ')
    assert obj is None and "Невалидный JSON" in err


def test_wrong_schema():
    obj, err = parse_reply(TutorReply, '{"message": "Что делает цикл?", "hint_level": 7}')
    assert obj is None and "TutorReply" in err


def test_code_with_quotes_and_backslashes():
    import json
    code = 'print("a\\nb")\nx = "it\'s"'
    text = json.dumps({"task_id": "t", "student_code": code})
    from schemas.messages import CheckRequest
    obj, err = parse_reply(CheckRequest, text)
    assert err is None and obj.student_code == code


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
