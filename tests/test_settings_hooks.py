"""Tests for settings_hooks - Claude Code .claude/settings.json integration."""

import json
import time
import uuid
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from claude_telemetry.settings_hooks import (
    _HOOK_CONFIG,
    _clear_state,
    _install_into,
    _load_state,
    _save_state,
    _state_file,
    cmd_notification,
    cmd_post_tool_use,
    cmd_pre_compact,
    cmd_pre_tool_use,
    cmd_stop,
    cmd_subagent_stop,
    cmd_user_prompt_submit,
    export_session_trace,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


import claude_telemetry.settings_hooks as sh_module


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Redirect session state files to a temporary directory."""
    monkeypatch.setattr(sh_module, "_STATE_DIR", tmp_path / "sessions")
    return tmp_path / "sessions"


@pytest.fixture
def sample_session_id():
    return "test-session-abc123"


@pytest.fixture
def minimal_state(sample_session_id):
    now = time.time()
    return {
        "session_id": sample_session_id,
        "start_time": now - 10,
        "stop_time": now,
        "prompt": "Write a hello world program",
        "model": "claude-opus-4-5",
        "metrics": {
            "input_tokens": 150,
            "output_tokens": 80,
            "tools_used": 2,
            "turns": 1,
        },
        "tools_used": ["Bash", "Read"],
        "events": [
            {
                "type": "user_prompt_submit",
                "timestamp": now - 10,
                "prompt": "Write a hello world program",
            },
            {
                "type": "pre_tool_use",
                "timestamp": now - 8,
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "tool_use_id": "tool-001",
            },
            {
                "type": "post_tool_use",
                "timestamp": now - 7,
                "tool_name": "Bash",
                "tool_response": {"stdout": "hello", "stderr": "", "returnCode": 0},
                "tool_use_id": "tool-001",
            },
        ],
    }


# ---------------------------------------------------------------------------
# State-file helpers
# ---------------------------------------------------------------------------


class TestStateHelpers:
    def test_load_state_returns_fresh_state_when_no_file(
        self, tmp_state_dir, sample_session_id
    ):
        state = _load_state(sample_session_id)
        assert state["session_id"] == sample_session_id
        assert state["metrics"]["input_tokens"] == 0
        assert state["events"] == []

    def test_save_and_load_roundtrip(self, tmp_state_dir, sample_session_id):
        original = _load_state(sample_session_id)
        original["prompt"] = "hello"
        original["metrics"]["tools_used"] = 3
        _save_state(sample_session_id, original)

        loaded = _load_state(sample_session_id)
        assert loaded["prompt"] == "hello"
        assert loaded["metrics"]["tools_used"] == 3

    def test_clear_state_removes_file(self, tmp_state_dir, sample_session_id):
        _save_state(sample_session_id, _load_state(sample_session_id))
        assert _state_file(sample_session_id).exists()

        _clear_state(sample_session_id)
        assert not _state_file(sample_session_id).exists()

    def test_clear_state_noop_when_no_file(self, tmp_state_dir, sample_session_id):
        """Should not raise if state file doesn't exist."""
        _clear_state(sample_session_id)  # no exception

    def test_load_state_returns_fresh_state_on_corrupt_file(
        self, tmp_state_dir, sample_session_id
    ):
        """A corrupt (partially-written) state file must not crash the hook."""
        # Simulate the "Extra data" corruption that happens with concurrent writes
        corrupt = '{"session_id": "x", "start_time": 1234}{"session_id": "x"}'
        _state_file(sample_session_id).write_text(corrupt)

        state = _load_state(sample_session_id)
        # Should silently recover and return a fresh state
        assert state["session_id"] == sample_session_id
        assert state["events"] == []
        assert state["metrics"]["input_tokens"] == 0

    def test_save_state_is_atomic(self, tmp_state_dir, sample_session_id):
        """_save_state must not leave .tmp files in the state directory."""
        state = _load_state(sample_session_id)
        state["prompt"] = "atomic test"
        _save_state(sample_session_id, state)

        # The destination file must exist and be valid JSON
        loaded = _load_state(sample_session_id)
        assert loaded["prompt"] == "atomic test"

        # No leftover .tmp files
        tmp_files = list(tmp_state_dir.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files left behind: {tmp_files}"


# ---------------------------------------------------------------------------
# Hook command handlers (via stdin simulation)
# ---------------------------------------------------------------------------


class TestHookCommands:
    """Test each hook command by simulating stdin input."""

    def _invoke(self, command_fn, stdin_data: dict, tmp_state_dir, monkeypatch):
        """Helper: simulate a hook invocation with JSON on stdin."""
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(stdin_data)))
        command_fn()

    def test_user_prompt_submit_initializes_state(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_user_prompt_submit,
            {"session_id": sample_session_id, "prompt": "Hello Claude"},
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        assert state["prompt"] == "Hello Claude"
        assert len(state["events"]) == 1
        assert state["events"][0]["type"] == "user_prompt_submit"

    def test_user_prompt_submit_generates_uuid_when_session_id_missing(
        self, tmp_state_dir, monkeypatch
    ):
        """When no session_id is provided, a UUID must be used instead of 'unknown'."""
        monkeypatch.setattr(
            "sys.stdin", StringIO(json.dumps({"prompt": "hello claude"}))
        )
        cmd_user_prompt_submit()

        # Find the state file that was created - there should be exactly one
        session_files = list((tmp_state_dir).glob("*.json"))
        assert len(session_files) == 1, "Expected exactly one session state file"

        state = json.loads(session_files[0].read_text())
        session_id = state["session_id"]

        # Must not be the literal "unknown"
        assert session_id != "unknown", (
            f"session_id should be a UUID, got {session_id!r}"
        )
        # Must be a valid UUID
        uuid.UUID(session_id)  # raises ValueError if not a valid UUID
        assert state["prompt"] == "hello claude"

    def test_user_prompt_submit_captures_model(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_user_prompt_submit,
            {
                "session_id": sample_session_id,
                "prompt": "Go",
                "model": "claude-opus-4-5",
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        assert state["model"] == "claude-opus-4-5"

    def test_pre_tool_use_appends_event(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_pre_tool_use,
            {
                "session_id": sample_session_id,
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "t1",
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        assert state["metrics"]["tools_used"] == 1
        assert "Bash" in state["tools_used"]
        assert state["events"][0]["type"] == "pre_tool_use"

    def test_pre_tool_use_deduplicates_tool_names(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        for _ in range(3):
            self._invoke(
                cmd_pre_tool_use,
                {"session_id": sample_session_id, "tool_name": "Bash"},
                tmp_state_dir,
                monkeypatch,
            )

        state = _load_state(sample_session_id)
        # tools_used list should not have duplicates
        assert state["tools_used"].count("Bash") == 1
        # metrics counter SHOULD count every call
        assert state["metrics"]["tools_used"] == 3

    def test_post_tool_use_appends_event(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_post_tool_use,
            {
                "session_id": sample_session_id,
                "tool_name": "Read",
                "tool_response": {"content": "file contents"},
                "tool_use_id": "t2",
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        ev = state["events"][0]
        assert ev["type"] == "post_tool_use"
        assert ev["tool_name"] == "Read"
        assert ev["tool_response"]["content"] == "file contents"

    def test_pre_compact_appends_event(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_pre_compact,
            {
                "session_id": sample_session_id,
                "trigger": "token_limit",
                "custom_instructions": "Keep the important stuff",
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        ev = state["events"][0]
        assert ev["type"] == "pre_compact"
        assert ev["trigger"] == "token_limit"
        assert ev["has_custom_instructions"] is True

    def test_subagent_stop_appends_event(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_subagent_stop,
            {
                "session_id": sample_session_id,
                "subagent_session_id": "sub-abc",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 30, "output_tokens": 15},
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        ev = state["events"][0]
        assert ev["type"] == "subagent_stop"
        assert ev["subagent_session_id"] == "sub-abc"
        assert ev["stop_reason"] == "end_turn"
        assert ev["input_tokens"] == 30
        assert ev["output_tokens"] == 15

    def test_subagent_stop_handles_flat_token_fields(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        """Token counts may be at the top level instead of under 'usage'."""
        self._invoke(
            cmd_subagent_stop,
            {
                "session_id": sample_session_id,
                "input_tokens": 25,
                "output_tokens": 10,
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        ev = state["events"][0]
        assert ev["input_tokens"] == 25
        assert ev["output_tokens"] == 10

    def test_notification_appends_event(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        self._invoke(
            cmd_notification,
            {
                "session_id": sample_session_id,
                "message": "Running linter...",
                "title": "Lint",
                "level": "info",
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        ev = state["events"][0]
        assert ev["type"] == "notification"
        assert ev["message"] == "Running linter..."
        assert ev["title"] == "Lint"
        assert ev["level"] == "info"

    def test_stop_stores_stop_time_before_export(
        self, tmp_state_dir, monkeypatch, sample_session_id, minimal_state
    ):
        """cmd_stop must set state['stop_time'] before calling export_session_trace."""
        _save_state(sample_session_id, minimal_state)

        captured = {}
        with patch(
            "claude_telemetry.settings_hooks.export_session_trace"
        ) as mock_export:
            mock_export.side_effect = lambda state, *a, **kw: captured.update(state)
            monkeypatch.setattr(
                "sys.stdin",
                StringIO(json.dumps({"session_id": sample_session_id, "stop_reason": "end_turn"})),
            )
            cmd_stop()

        assert "stop_time" in captured, "stop_time must be stored before export"

    def test_stop_exports_trace_and_clears_state(
        self, tmp_state_dir, monkeypatch, sample_session_id, minimal_state
    ):
        _save_state(sample_session_id, minimal_state)

        # Patch export so we don't need a real OTEL backend
        with patch(
            "claude_telemetry.settings_hooks.export_session_trace"
        ) as mock_export:
            monkeypatch.setattr(
                "sys.stdin",
                StringIO(
                    json.dumps(
                        {
                            "session_id": sample_session_id,
                            "stop_reason": "end_turn",
                        }
                    )
                ),
            )
            cmd_stop()

        mock_export.assert_called_once()
        call_args = mock_export.call_args
        assert call_args[0][0]["session_id"] == sample_session_id
        assert call_args[0][1] == "end_turn"

        # State file should have been cleaned up
        assert not _state_file(sample_session_id).exists()


# ---------------------------------------------------------------------------
# export_session_trace
# ---------------------------------------------------------------------------


class TestExportSessionTrace:
    def test_creates_otel_span(self, mocker, monkeypatch, minimal_state):
        """export_session_trace should create a root span with expected attributes."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state, stop_reason="end_turn")

        # The very first start_span call creates the root session span
        assert mock_tracer.start_span.called
        first_call = mock_tracer.start_span.call_args_list[0]
        attrs = first_call[1]["attributes"]
        assert attrs["session_id"] == minimal_state["session_id"]
        assert attrs["gen_ai.usage.input_tokens"] == 150
        assert attrs["gen_ai.usage.output_tokens"] == 80
        assert attrs["tools_used"] == 2
        assert attrs["stop_reason"] == "end_turn"

    def test_span_title_uses_prompt_preview(self, mocker, monkeypatch, minimal_state):
        """Span title should contain a truncated prompt."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = MagicMock()

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state)

        span_title = mock_tracer.start_span.call_args_list[0][0][0]
        assert "Write a hello world" in span_title

    def test_long_prompt_is_truncated_in_title(self, mocker, monkeypatch):
        """Prompt > 60 chars should be truncated with '...' in span title."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = MagicMock()

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        state = {
            "session_id": "s1",
            "start_time": time.time(),
            "stop_time": time.time(),
            "prompt": "A" * 100,
            "model": "unknown",
            "metrics": {
                "input_tokens": 0,
                "output_tokens": 0,
                "tools_used": 0,
                "turns": 0,
            },
            "tools_used": [],
            "events": [],
        }
        export_session_trace(state)

        span_title = mock_tracer.start_span.call_args_list[0][0][0]
        assert "..." in span_title

    def test_events_create_child_spans(self, mocker, monkeypatch, minimal_state):
        """Each event category should produce a dedicated child span."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state)

        span_names = [c[0][0] for c in mock_tracer.start_span.call_args_list]
        # Session root span (contains emoji or "Claude Session")
        assert any("🤖" in n or "Claude Session" in n for n in span_names)
        # Turn span
        assert any("Turn" in n or "👤" in n for n in span_names)
        # Tool span
        assert any("🔧" in n for n in span_names)

    def test_empty_session_does_not_crash(self, mocker, monkeypatch):
        """export_session_trace should not crash for an empty session."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = MagicMock()

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace({})  # no exception

    def test_provider_shutdown_called_after_force_flush(
        self, mocker, monkeypatch, minimal_state
    ):
        """shutdown() must be called after force_flush() so the daemon exporter
        thread is joined before sys.exit() destroys buffered span data."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = MagicMock()

        mock_provider = MagicMock()
        call_order = []
        mock_provider.force_flush.side_effect = lambda *a, **kw: call_order.append(
            "force_flush"
        )
        mock_provider.shutdown.side_effect = lambda *a, **kw: call_order.append(
            "shutdown"
        )

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer_provider",
            return_value=mock_provider,
        )

        export_session_trace(minimal_state, stop_reason="end_turn")

        assert "force_flush" in call_order, "force_flush() was not called"
        assert "shutdown" in call_order, "shutdown() was not called"
        assert call_order.index("force_flush") < call_order.index("shutdown"), (
            "force_flush() must be called before shutdown()"
        )

    def test_span_hierarchy_session_turn_tool(self, mocker, monkeypatch, minimal_state):
        """Tool spans must be children of the turn span, which is a child of session."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        test_tracer = provider.get_tracer("test")

        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=test_tracer,
        )
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer_provider",
            return_value=provider,
        )

        export_session_trace(minimal_state)

        spans = exporter.get_finished_spans()
        assert len(spans) >= 3, "Expected at least session + turn + tool spans"

        # The session span is the only root span (no parent)
        session_spans = [s for s in spans if s.parent is None]
        turn_spans = [s for s in spans if "👤" in s.name or "Turn" in s.name]
        tool_spans = [s for s in spans if "🔧" in s.name]

        assert len(session_spans) == 1, f"Expected 1 session span, got {len(session_spans)}"
        assert len(turn_spans) >= 1, "Expected at least 1 turn span"
        assert len(tool_spans) >= 1, "Expected at least 1 tool span"

        session_span = session_spans[0]
        turn_span = turn_spans[0]
        tool_span = tool_spans[0]

        # Turn is a direct child of session
        assert turn_span.parent is not None
        assert turn_span.parent.span_id == session_span.context.span_id, (
            "Turn span must be a child of the session span"
        )

        # Tool is a child of the turn (not directly of session)
        assert tool_span.parent is not None
        assert tool_span.parent.span_id == turn_span.context.span_id, (
            "Tool span must be a child of the turn span, not directly of session"
        )

    def test_tool_matched_by_tool_use_id(self, mocker, monkeypatch):
        """pre_tool_use and post_tool_use with matching tool_use_id produce one span."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        test_tracer = provider.get_tracer("test")

        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=test_tracer,
        )
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer_provider",
            return_value=provider,
        )

        now = time.time()
        state = {
            "session_id": "s-tool",
            "start_time": now - 5,
            "stop_time": now,
            "prompt": "Test",
            "model": "claude-opus-4-5",
            "metrics": {"input_tokens": 0, "output_tokens": 0, "tools_used": 1, "turns": 1},
            "tools_used": ["Bash"],
            "events": [
                {"type": "user_prompt_submit", "timestamp": now - 5, "prompt": "Test"},
                {
                    "type": "pre_tool_use",
                    "timestamp": now - 3,
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hi"},
                    "tool_use_id": "tid-42",
                },
                {
                    "type": "post_tool_use",
                    "timestamp": now - 2,
                    "tool_name": "Bash",
                    "tool_response": "hi",
                    "tool_use_id": "tid-42",
                },
            ],
        }
        export_session_trace(state)

        tool_spans = [s for s in exporter.get_finished_spans() if "🔧" in s.name]
        assert len(tool_spans) == 1, (
            f"Expected exactly 1 tool span from matched pair, got {len(tool_spans)}"
        )

    def test_compaction_and_notification_are_session_children(
        self, mocker, monkeypatch
    ):
        """PreCompact and Notification spans must be direct children of the session."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        test_tracer = provider.get_tracer("test")

        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=test_tracer,
        )
        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer_provider",
            return_value=provider,
        )

        now = time.time()
        state = {
            "session_id": "s-extra",
            "start_time": now - 10,
            "stop_time": now,
            "prompt": "Test",
            "model": "claude-opus-4-5",
            "metrics": {"input_tokens": 0, "output_tokens": 0, "tools_used": 0, "turns": 0},
            "tools_used": [],
            "events": [
                {"type": "pre_compact", "timestamp": now - 8, "trigger": "auto", "has_custom_instructions": False},
                {"type": "notification", "timestamp": now - 5, "message": "Running tests", "level": "info", "title": ""},
                {"type": "subagent_stop", "timestamp": now - 3, "subagent_session_id": "sub-1", "stop_reason": "end_turn", "input_tokens": 20, "output_tokens": 10},
            ],
        }
        export_session_trace(state)

        spans = exporter.get_finished_spans()
        # The session span is the only root span (no parent)
        session_spans = [s for s in spans if s.parent is None]
        compact_spans = [s for s in spans if "🗜️" in s.name]
        notif_spans = [s for s in spans if "🔔" in s.name]
        sub_spans = [s for s in spans if "Subagent" in s.name]

        assert len(session_spans) == 1
        assert len(compact_spans) == 1
        assert len(notif_spans) == 1
        assert len(sub_spans) == 1

        session_id = session_spans[0].context.span_id
        for child_span in [compact_spans[0], notif_spans[0], sub_spans[0]]:
            assert child_span.parent is not None
            assert child_span.parent.span_id == session_id, (
                f"{child_span.name!r} must be a direct child of the session span"
            )


# ---------------------------------------------------------------------------
# Install command
# ---------------------------------------------------------------------------


class TestInstallInto:
    def test_creates_settings_file_when_absent(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        _install_into(settings_path)

        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        for event in (
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "PreCompact",
            "SubagentStop",
            "Notification",
        ):
            assert event in settings["hooks"]

    def test_merges_into_existing_settings(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"theme": "dark", "hooks": {}}))

        _install_into(settings_path)

        settings = json.loads(settings_path.read_text())
        # Existing keys preserved
        assert settings["theme"] == "dark"
        # Hooks added
        assert "UserPromptSubmit" in settings["hooks"]

    def test_does_not_duplicate_hooks(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        # Install twice
        _install_into(settings_path)
        _install_into(settings_path)

        settings = json.loads(settings_path.read_text())
        # Each event should have exactly one matcher
        for event_name in _HOOK_CONFIG:
            assert len(settings["hooks"][event_name]) == 1

    def test_preserves_existing_hooks(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "my-custom-hook"}]}
                ]
            }
        }
        settings_path.write_text(json.dumps(existing))

        _install_into(settings_path)

        settings = json.loads(settings_path.read_text())
        pre_tool_hooks = settings["hooks"]["PreToolUse"]
        commands = [
            h["command"]
            for matcher in pre_tool_hooks
            for h in matcher.get("hooks", [])
        ]
        assert "my-custom-hook" in commands
        assert "claude2sunfire-hook pre-tool-use" in commands
        assert "claude-telemetry-hook pre-tool-use" not in commands
