from __future__ import annotations

from mail_runner.run_result_capsule import (
    RUN_RESULT_BEGIN_MARKER,
    RUN_RESULT_END_MARKER,
    StructuredRunResult,
    parse_run_result_capsule,
    render_run_result_capsule,
    strip_run_result_capsules,
)


def test_run_result_capsule_round_trip() -> None:
    rendered = render_run_result_capsule(
        StructuredRunResult(
            changed_files=["src/app.py", "tests/test_app.py"],
            tests_passed=True,
            error_type=None,
            error_message=None,
        )
    )

    parsed = parse_run_result_capsule(rendered)

    assert parsed is not None
    assert parsed.changed_files == ["src/app.py", "tests/test_app.py"]
    assert parsed.tests_passed is True
    assert parsed.error_type is None
    assert parsed.error_message is None


def test_strip_run_result_capsules_keeps_human_reply() -> None:
    text = "\n".join(
        [
            "Implemented the requested fix.",
            "",
            RUN_RESULT_BEGIN_MARKER,
            "changed_files: src/app.py | tests/test_app.py",
            "tests_passed: false",
            "error_type: validation_error",
            "error_message: pytest failed",
            RUN_RESULT_END_MARKER,
        ]
    )

    stripped = strip_run_result_capsules(text)

    assert stripped == "Implemented the requested fix."
    assert parse_run_result_capsule(stripped) is None
