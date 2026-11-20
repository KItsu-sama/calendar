import io

from rich.console import Console

from cal_engine.ui.terminal import Terminal, terminal


def _make_console() -> Console:
    return Console(file=io.StringIO(), width=200)


def test_output_text_is_captured():
    console = _make_console()
    output = Terminal(console=console)
    output.print("plain message")
    assert "plain message" in console.file.getvalue()


def test_success_is_colored_and_symbolized():
    console = _make_console()
    output = Terminal(console=console)
    output.success("activity saved")
    text = console.file.getvalue()
    assert "activity saved" in text
    assert "✓" in text


def test_error_is_colored_and_symbolized():
    console = _make_console()
    output = Terminal(console=console)
    output.error("invalid command")
    text = console.file.getvalue()
    assert "invalid command" in text
    assert "✗" in text


def test_warning_is_colored_and_symbolized():
    console = _make_console()
    output = Terminal(console=console)
    output.warning("unsaved changes")
    text = console.file.getvalue()
    assert "unsaved changes" in text
    assert "!" in text


def test_info_is_colored_and_symbolized():
    console = _make_console()
    output = Terminal(console=console)
    output.info("loading calendar")
    text = console.file.getvalue()
    assert "loading calendar" in text
    assert "->" in text


def test_rule_prints_title():
    console = _make_console()
    output = Terminal(console=console)
    output.rule("Calendars")
    assert "Calendars" in console.file.getvalue()


def test_table_prints_rows_and_columns():
    console = _make_console()
    output = Terminal(console=console)
    output.table(
        [
            {"name": "lunch", "priority": "high"},
            {"name": "gym", "priority": "low"},
        ]
    )
    text = console.file.getvalue()
    assert "lunch" in text
    assert "gym" in text
    assert "priority" in text


def test_table_empty_reports_no_results():
    console = _make_console()
    output = Terminal(console=console)
    output.table([])
    assert "No results" in console.file.getvalue()


def test_json_output_contains_serialized_value():
    console = _make_console()
    output = Terminal(console=console)
    output.json({"activity": "lunch", "priority": "high"})
    text = console.file.getvalue()
    assert "activity" in text
    assert "lunch" in text
    assert "priority" in text


def test_default_terminal_instance_is_configured():
    assert terminal is not None
    assert terminal.console is not None
