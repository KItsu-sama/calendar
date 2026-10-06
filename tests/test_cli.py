import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args):
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "main.py"), *map(str, args)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _add(path, name, *definition):
    load = ("--load", path) if path.exists() else ()
    result = _run(
        "--reference-date",
        "2026-09-21",
        *load,
        "--save",
        path,
        "--add",
        definition[0],
        name,
        *definition[1:],
    )
    assert result.returncode == 0, result.stderr


def test_legacy_definition_still_prints_structured_json():
    result = _run("--fixed", "school", "--daily", "--time", "07:00-11:30", "--except", "sunday")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["id"] == "school"


def test_move_change_schedule_and_free_recalculate(tmp_path):
    path = tmp_path / "state.json"
    _add(path, "school", "--fixed", "--daily", "--time", "07:00-11:30", "--except", "sunday")
    _add(path, "lunch", "--fixed", "--daily", "--time", "13:00-14:00")
    _add(path, "math", "--fixed", "--wed", "--time", "15:00-16:00", "--changeable", "--move-to", "sat")

    moved = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--move", "math", "--to", "sat", "--date", "2026-09-23",
    )
    assert moved.returncode == 0, moved.stderr
    assert json.loads(moved.stdout)["status"] == "DIRECT_ADD"

    changed = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--change", "school", "--time", "07:30-12:00",
    )
    assert changed.returncode == 0, changed.stderr

    free = _run("--reference-date", "2026-09-21", "--load", path, "--free", "--date", "2026-09-21")
    intervals = json.loads(free.stdout)["free_time"]
    assert [(item["start"][11:16], item["end"][11:16]) for item in intervals] == [
        ("00:00", "07:30"),
        ("12:00", "13:00"),
        ("14:00", "00:00"),
    ]

    schedule = _run(
        "--reference-date", "2026-09-21", "--load", path,
        "--schedule", "--from", "2026-09-23", "--to", "2026-09-26",
    )
    assert schedule.returncode == 0, schedule.stderr
    items = json.loads(schedule.stdout)["schedule"]
    assert not any(item["start"].startswith("2026-09-23T15:") for item in items)
    assert any(item["activity_id"] == "math" and item["start"].startswith("2026-09-26T15:") for item in items)


def test_add_cancel_exception_and_remove_actions(tmp_path):
    path = tmp_path / "state.json"
    _add(path, "school", "--fixed", "--daily", "--time", "07:00-11:30", "--except", "sunday")
    _add(path, "lunch", "--fixed", "--daily", "--time", "13:00-14:00")
    _add(path, "math", "--fixed", "--wed", "--time", "15:00-16:00", "--changeable", "--move-to", "sat")

    modified = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--exception", "school", "--date", "2026-09-21", "--time", "07:30-08:00",
    )
    assert modified.returncode == 0, modified.stderr
    assert json.loads(modified.stdout)["status"] == "DIRECT_ADD"

    cancelled = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--cancel", "lunch", "--date", "2026-09-21",
    )
    assert cancelled.returncode == 0, cancelled.stderr
    assert json.loads(cancelled.stdout)["status"] == "CANCELLED"

    exception = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--exception", "math", "--date", "2026-09-23", "--move", "sat",
    )
    assert exception.returncode == 0, exception.stderr
    assert json.loads(exception.stdout)["status"] == "DIRECT_ADD"

    removed = _run(
        "--reference-date", "2026-09-21", "--load", path, "--save", path,
        "--remove", "school",
    )
    assert removed.returncode == 0, removed.stderr
    state = json.loads(path.read_text(encoding="utf-8"))
    assert [rule["id"] for rule in state["rules"]] == ["lunch", "math"]


def test_preview_conflicts_and_generate_prompt(tmp_path):
    path = tmp_path / "state.json"
    _add(path, "school", "--fixed", "--daily", "--time", "07:00-11:30", "--except", "sunday")
    _add(path, "math", "--fixed", "--wed", "--time", "15:00-16:00", "--changeable", "--move-to", "sat")
    _add(path, "gaming", "--optional", "--wed", "--time", "16:00-17:00")
    _add(path, "homework", "--todo", "--min", "30m", "--max", "90m", "--due", "fri", "21:00")
    args = ("--reference-date", "2026-09-21", "--load", path)

    preview = _run(*args, "--preview", "--fixed", "exam-review", "--wed", "--time", "15:00-18:00")
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["status"] == "REQUIRES_AI"

    conflicts = _run(*args, "--conflicts", "--fixed", "exam-review", "--wed", "--time", "15:00-18:00")
    assert conflicts.returncode == 0, conflicts.stderr
    assert len(json.loads(conflicts.stdout)["conflicts"]) == 2

    prompt = _run(*args, "--generate-prompt", "--fixed", "exam-review", "--wed", "--time", "15:00-18:00")
    assert prompt.returncode == 0, prompt.stderr
    assert "[CURRENT SCHEDULE MATRIX - TARGET WINDOW]" in prompt.stdout
    assert "homework" in prompt.stdout
    assert "[REQUIRED OUTPUT FORMAT]" in prompt.stdout
