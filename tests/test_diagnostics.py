import io

import pytest

from cal_engine.debuger.context import DiagnosticContext, diagnostic_context
from cal_engine.debuger.logger import configure_logging
from cal_engine.debuger.trace import trace_stage


def test_trace_records_completed_stages_and_duration():
    output = io.StringIO()
    configure_logging(True, output)
    context = DiagnosticContext(command="--schedule", arguments=("--debug", "--schedule"))

    try:
        with diagnostic_context(context):
            with trace_stage("state.load"):
                pass

        report = context.report()
        assert report["stages"][0]["name"] == "state.load"
        assert report["stages"][0]["status"] == "completed"
        assert report["stages"][0]["duration_ms"] >= 0
        assert "TRACE start state.load" in output.getvalue()
        assert "TRACE complete state.load" in output.getvalue()
    finally:
        configure_logging(False)


def test_trace_records_failure_and_reraises():
    output = io.StringIO()
    configure_logging(True, output)
    context = DiagnosticContext(command="--schedule", arguments=("--debug", "--schedule"))

    try:
        with diagnostic_context(context):
            with pytest.raises(RuntimeError, match="resolver failed"):
                with trace_stage("schedule.build"):
                    raise RuntimeError("resolver failed")

        report = context.report()
        assert report["failure"]["type"] == "RuntimeError"
        assert report["failure"]["stage"] == "schedule.build"
        assert report["failure"]["location"] != "unknown"
        assert report["stages"][0]["status"] == "failed"
        assert "TRACE failed schedule.build" in output.getvalue()
    finally:
        configure_logging(False)
