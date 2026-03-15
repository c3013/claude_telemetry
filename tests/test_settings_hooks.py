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
    cmd_message_complete,
    cmd_post_tool_use,
    cmd_pre_compact,
    cmd_pre_tool_use,
    cmd_stop,
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
    return {
        "session_id": sample_session_id,
        "start_time": time.time() - 10,
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
                "timestamp": time.time() - 10,
                "prompt": "Write a hello world program",
            },
            {
                "type": "pre_tool_use",
                "timestamp": time.time() - 8,
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "tool_use_id": "tool-001",
            },
            {
                "type": "post_tool_use",
                "timestamp": time.time() - 7,
                "tool_name": "Bash",
                "tool_response": {"stdout": "hello", "stderr": "", "returnCode": 0},
                "tool_use_id": "tool-001",
            },
            {
                "type": "message_complete",
                "timestamp": time.time() - 5,
                "input_tokens": 150,
                "output_tokens": 80,
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

    def test_message_complete_updates_token_counts(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        # Two turns
        for in_tok, out_tok in [(100, 200), (50, 75)]:
            self._invoke(
                cmd_message_complete,
                {
                    "session_id": sample_session_id,
                    "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                },
                tmp_state_dir,
                monkeypatch,
            )

        state = _load_state(sample_session_id)
        assert state["metrics"]["input_tokens"] == 150
        assert state["metrics"]["output_tokens"] == 275
        assert state["metrics"]["turns"] == 2

    def test_message_complete_handles_flat_token_fields(
        self, tmp_state_dir, monkeypatch, sample_session_id
    ):
        """Token counts may be at the top level instead of under 'usage'."""
        self._invoke(
            cmd_message_complete,
            {
                "session_id": sample_session_id,
                "input_tokens": 30,
                "output_tokens": 60,
            },
            tmp_state_dir,
            monkeypatch,
        )

        state = _load_state(sample_session_id)
        assert state["metrics"]["input_tokens"] == 30
        assert state["metrics"]["output_tokens"] == 60

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
        """export_session_trace should create a span with expected attributes."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            mock_span
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state, stop_reason="end_turn")

        mock_tracer.start_as_current_span.assert_called_once()
        call_kwargs = mock_tracer.start_as_current_span.call_args[1]
        attrs = call_kwargs["attributes"]
        assert attrs["session_id"] == minimal_state["session_id"]
        assert attrs["gen_ai.usage.input_tokens"] == 150
        assert attrs["gen_ai.usage.output_tokens"] == 80
        assert attrs["tools_used"] == 2
        assert attrs["stop_reason"] == "end_turn"

    def test_span_title_uses_prompt_preview(self, mocker, monkeypatch, minimal_state):
        """Span title should contain a truncated prompt."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            MagicMock()
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state)

        span_title = mock_tracer.start_as_current_span.call_args[0][0]
        assert "Write a hello world" in span_title

    def test_long_prompt_is_truncated_in_title(self, mocker, monkeypatch):
        """Prompt > 60 chars should be truncated with '...' in span title."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            MagicMock()
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        state = {
            "session_id": "s1",
            "start_time": time.time(),
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

        span_title = mock_tracer.start_as_current_span.call_args[0][0]
        assert "..." in span_title

    def test_events_are_replayed_as_span_events(
        self, mocker, monkeypatch, minimal_state
    ):
        """All collected events should be added to the span."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            mock_span
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

        mocker.patch(
            "claude_telemetry.settings_hooks.trace.get_tracer",
            return_value=mock_tracer,
        )
        mocker.patch("claude_telemetry.settings_hooks.configure_telemetry")

        export_session_trace(minimal_state)

        # Should have an add_event call for each event type + the "Completed" event
        event_names = [call[0][0] for call in mock_span.add_event.call_args_list]
        assert any("User prompt" in n for n in event_names)
        assert any("Tool started" in n for n in event_names)
        assert any("Tool completed" in n for n in event_names)
        assert any("Turn completed" in n for n in event_names)
        assert any("Completed" in n for n in event_names)

    def test_empty_session_does_not_crash(self, mocker, monkeypatch):
        """export_session_trace should not crash for an empty session."""
        monkeypatch.setenv("CLAUDE_TELEMETRY_DEBUG", "1")

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            MagicMock()
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

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
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s, *a: (
            MagicMock()
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = (
            lambda s, *a: False
        )

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
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
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
        assert "claude-telemetry-hook pre-tool-use" in commands
