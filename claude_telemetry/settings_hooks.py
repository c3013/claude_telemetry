"""Standalone hook runner for Claude Code .claude/settings.json integration.

This module provides CLI commands that can be configured as Claude Code hooks
in .claude/settings.json to enable OpenTelemetry tracing when running the
standard ``claude`` CLI - no wrapper needed.

How it works
------------
Claude Code invokes hook commands as subprocesses, passing event data as JSON
on stdin.  Because each invocation is a separate process, session state is
persisted in a JSON file under a user-specific cache directory
(``~/.cache/claude_telemetry/sessions/``).

Data-collection hooks (``user-prompt-submit``, ``pre-tool-use``,
``post-tool-use``, ``message-complete``, ``pre-compact``) simply append
event records to the state file.  The ``stop`` hook reads the complete state
and exports one OTel session span with all events attached, then cleans up.

Quick-start
-----------
1. Install the package::

       pip install claude2sunfire

2. Configure your telemetry backend (one of)::

       export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.honeycomb.io"
       export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<key>"
       # or
       export LOGFIRE_TOKEN="<token>"
       # or
       export CLAUDE_TELEMETRY_DEBUG=1   # console output

3. Add hooks to ``~/.claude/settings.json`` (or run ``claude2sunfire-hook install``)::

       {
         "hooks": {
           "UserPromptSubmit": [
             {"hooks": [{"type": "command",
                         "command": "claude2sunfire-hook user-prompt-submit"}]}
           ],
           "PreToolUse": [
             {"hooks": [{"type": "command",
                         "command": "claude2sunfire-hook pre-tool-use"}]}
           ],
           "PostToolUse": [
             {"hooks": [{"type": "command",
                         "command": "claude2sunfire-hook post-tool-use"}]}
           ],
           "Stop": [
             {"hooks": [{"type": "command",
                         "command": "claude2sunfire-hook stop"}]}
           ]
         }
       }

4. Use ``claude`` as normal - traces appear in your OTel backend automatically.
"""

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from opentelemetry import trace

from claude_telemetry.helpers.logger import logger
from claude_telemetry.hooks import (
    add_response_to_event_data,
    create_completion_title,
    create_event_data,
    create_tool_title,
)
from claude_telemetry.telemetry import configure_telemetry

load_dotenv()

# ---------------------------------------------------------------------------
# Typer application
# ---------------------------------------------------------------------------

hook_app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    help=(
        "[bold]Claude Code settings.json hook commands[/bold]\n\n"
        "Add these commands to .claude/settings.json to enable OTel tracing "
        "when running the standard [cyan]claude[/cyan] CLI."
    ),
)

# ---------------------------------------------------------------------------
# State-file helpers
# ---------------------------------------------------------------------------

_STATE_DIR = Path.home() / ".cache" / "claude_telemetry" / "sessions"


def _state_dir() -> Path:
    """Return (and create) the state directory."""
    _STATE_DIR.mkdir(exist_ok=True, parents=True)
    return _STATE_DIR


def _state_file(session_id: str) -> Path:
    return _state_dir() / f"{session_id}.json"


def _load_state(session_id: str) -> dict:
    """Load persisted session state; return fresh state if none exists.

    If the state file exists but cannot be parsed (e.g. because a concurrent
    hook process left it in a partially-written state), the corrupt file is
    discarded and a fresh state is returned rather than crashing.
    """
    sf = _state_file(session_id)
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except json.JSONDecodeError:
            logger.warning(
                f"State file for session {session_id} is corrupted; "
                "discarding and starting fresh."
            )
    return {
        "session_id": session_id,
        "start_time": time.time(),
        "prompt": "",
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


def _save_state(session_id: str, state: dict) -> None:
    """Persist session state to file atomically.

    Writes to a temporary file in the same directory then calls
    ``os.replace()``, which is atomic on POSIX systems.  This prevents a
    concurrent hook process from reading a partially-written file.
    """
    dest = _state_file(session_id)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(state))
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clear_state(session_id: str) -> None:
    """Remove session state file."""
    sf = _state_file(session_id)
    if sf.exists():
        sf.unlink()


# ---------------------------------------------------------------------------
# stdin helpers
# ---------------------------------------------------------------------------


def _read_stdin_json() -> dict:
    """Read and parse JSON sent by Claude Code on stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


# ---------------------------------------------------------------------------
# OTel export (used by the Stop hook)
# ---------------------------------------------------------------------------


def export_session_trace(state: dict, stop_reason: str = "end_turn") -> None:
    """
    Create and export a single OTel session span from persisted state.

    This is called by the ``stop`` hook command after the Claude Code session
    ends.  It reconstructs the complete session timeline from the accumulated
    event records and exports one root span with child events attached.

    Args:
        state: Session state dict loaded from the state file.
        stop_reason: Reason the session stopped (e.g. ``"end_turn"``).
    """
    configure_telemetry()

    session_id = state.get("session_id", "unknown")
    prompt = state.get("prompt", "")
    metrics = state.get("metrics", {})
    events = state.get("events", [])
    start_time = state.get("start_time", time.time())

    # Build a human-readable span title from the prompt
    prompt_preview = (prompt[:60] + "...") if len(prompt) > 60 else prompt
    span_title = f"🤖 {prompt_preview}" if prompt else "Claude Session"

    tracer = trace.get_tracer("claude-telemetry")

    with tracer.start_as_current_span(
        span_title,
        attributes={
            "prompt": prompt,
            "session_id": session_id,
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": state.get("model", "unknown"),
            "gen_ai.response.model": state.get("model", "unknown"),
            "gen_ai.usage.input_tokens": metrics.get("input_tokens", 0),
            "gen_ai.usage.output_tokens": metrics.get("output_tokens", 0),
            "tools_used": metrics.get("tools_used", 0),
            "tool_names": ",".join(state.get("tools_used", [])),
            "turns": metrics.get("turns", 0),
            "stop_reason": stop_reason,
        },
    ) as session_span:
        # Replay collected events in chronological order
        for ev in events:
            ev_type = ev.get("type")

            if ev_type == "user_prompt_submit":
                session_span.add_event(
                    "👤 User prompt submitted",
                    {"prompt": ev.get("prompt", "")},
                )

            elif ev_type == "pre_tool_use":
                tool_name = ev.get("tool_name", "unknown")
                tool_input = ev.get("tool_input", {})
                tool_title = create_tool_title(tool_name, tool_input)
                event_data = create_event_data(tool_name, tool_input)
                session_span.add_event(f"🔧 Tool started: {tool_title}", event_data)

            elif ev_type == "post_tool_use":
                tool_name = ev.get("tool_name", "unknown")
                tool_response = ev.get("tool_response")
                completion_title = create_completion_title(tool_name, tool_response)
                event_data = {"tool_name": tool_name}
                add_response_to_event_data(event_data, tool_response)
                session_span.add_event(
                    f"✅ Tool completed: {completion_title}", event_data
                )

            elif ev_type == "message_complete":
                session_span.add_event(
                    "Turn completed",
                    {
                        "input_tokens": ev.get("input_tokens", 0),
                        "output_tokens": ev.get("output_tokens", 0),
                    },
                )

            elif ev_type == "pre_compact":
                session_span.add_event(
                    "Context compaction",
                    {
                        "trigger": ev.get("trigger", "unknown"),
                        "has_custom_instructions": ev.get(
                            "has_custom_instructions", False
                        ),
                    },
                )

        session_span.add_event("🎉 Completed", {"stop_reason": stop_reason})

    # Force-flush then shut down so spans are fully exported before the
    # process exits.  BatchSpanProcessor uses a daemon worker thread; without
    # an explicit shutdown() that thread is killed by sys.exit() before the
    # HTTP request carrying the span data completes.
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
    if hasattr(provider, "shutdown"):
        provider.shutdown()

    duration = time.time() - start_time
    logger.info(
        f"✅ Session traced | "
        f"{metrics.get('input_tokens', 0)} in, "
        f"{metrics.get('output_tokens', 0)} out | "
        f"{metrics.get('tools_used', 0)} tools | "
        f"{duration:.1f}s"
    )


# ---------------------------------------------------------------------------
# Hook commands
# ---------------------------------------------------------------------------


@hook_app.command("user-prompt-submit")
def cmd_user_prompt_submit() -> None:
    """
    Handle UserPromptSubmit hook - initialize session state.

    Claude Code calls this when the user submits a prompt.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())
    prompt = event.get("prompt", "")

    state = _load_state(session_id)
    state["start_time"] = time.time()
    state["prompt"] = prompt

    # Capture model if provided in the event
    if "model" in event:
        state["model"] = event["model"]

    state["events"].append(
        {
            "type": "user_prompt_submit",
            "timestamp": time.time(),
            "prompt": prompt,
        }
    )

    _save_state(session_id, state)
    logger.debug(f"Hook: UserPromptSubmit for session {session_id}")


@hook_app.command("pre-tool-use")
def cmd_pre_tool_use() -> None:
    """
    Handle PreToolUse hook - record tool-start event.

    Claude Code calls this before each tool execution.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())
    tool_name = event.get("tool_name", "unknown")
    tool_input = event.get("tool_input", {})
    tool_use_id = event.get("tool_use_id", f"{tool_name}_{time.time()}")

    state = _load_state(session_id)
    state["metrics"]["tools_used"] = state["metrics"].get("tools_used", 0) + 1
    if tool_name not in state["tools_used"]:
        state["tools_used"].append(tool_name)

    state["events"].append(
        {
            "type": "pre_tool_use",
            "timestamp": time.time(),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
        }
    )

    _save_state(session_id, state)
    logger.debug(f"Hook: PreToolUse {tool_name} for session {session_id}")


@hook_app.command("post-tool-use")
def cmd_post_tool_use() -> None:
    """
    Handle PostToolUse hook - record tool-completion event.

    Claude Code calls this after each tool execution.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())
    tool_name = event.get("tool_name", "unknown")
    tool_response = event.get("tool_response")
    tool_use_id = event.get("tool_use_id")

    state = _load_state(session_id)
    state["events"].append(
        {
            "type": "post_tool_use",
            "timestamp": time.time(),
            "tool_name": tool_name,
            "tool_response": tool_response,
            "tool_use_id": tool_use_id,
        }
    )

    _save_state(session_id, state)
    logger.debug(f"Hook: PostToolUse {tool_name} for session {session_id}")


@hook_app.command("message-complete")
def cmd_message_complete() -> None:
    """
    Handle MessageComplete hook - update cumulative token counts.

    Claude Code calls this when an assistant message is complete.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())

    # Token usage may be nested under a "usage" key or at the top level
    usage = event.get("usage") or {}
    input_tokens = usage.get("input_tokens", 0) or event.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0) or event.get("output_tokens", 0)

    state = _load_state(session_id)
    state["metrics"]["input_tokens"] = (
        state["metrics"].get("input_tokens", 0) + input_tokens
    )
    state["metrics"]["output_tokens"] = (
        state["metrics"].get("output_tokens", 0) + output_tokens
    )
    state["metrics"]["turns"] = state["metrics"].get("turns", 0) + 1

    state["events"].append(
        {
            "type": "message_complete",
            "timestamp": time.time(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    )

    _save_state(session_id, state)
    logger.debug(f"Hook: MessageComplete for session {session_id}")


@hook_app.command("pre-compact")
def cmd_pre_compact() -> None:
    """
    Handle PreCompact hook - record context-compaction event.

    Claude Code calls this before compacting the context window.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())
    trigger = event.get("trigger", "unknown")
    has_custom = event.get("custom_instructions") is not None

    state = _load_state(session_id)
    state["events"].append(
        {
            "type": "pre_compact",
            "timestamp": time.time(),
            "trigger": trigger,
            "has_custom_instructions": has_custom,
        }
    )

    _save_state(session_id, state)
    logger.debug(f"Hook: PreCompact for session {session_id}")


@hook_app.command("stop")
def cmd_stop() -> None:
    """
    Handle Stop hook - export the complete OTel trace for this session.

    Claude Code calls this when the agent stops.  This command reads the
    accumulated session state, creates a single OTel span with all events
    attached, exports it to the configured backend, and cleans up.
    """
    event = _read_stdin_json()
    session_id = event.get("session_id") or str(uuid.uuid4())
    stop_reason = event.get("stop_reason", "end_turn")

    state = _load_state(session_id)
    export_session_trace(state, stop_reason)
    _clear_state(session_id)


# ---------------------------------------------------------------------------
# Install command
# ---------------------------------------------------------------------------

_HOOK_CONFIG = {
    "UserPromptSubmit": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "claude2sunfire-hook user-prompt-submit",
                }
            ]
        }
    ],
    "PreToolUse": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "claude2sunfire-hook pre-tool-use",
                }
            ]
        }
    ],
    "PostToolUse": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "claude2sunfire-hook post-tool-use",
                }
            ]
        }
    ],
    "Stop": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "claude2sunfire-hook stop",
                }
            ]
        }
    ],
}


@hook_app.command("install")
def cmd_install(
    user: Annotated[
        bool,
        typer.Option(
            "--user/--no-user",
            help="Install into user-level settings (~/.claude/settings.json)",
        ),
    ] = True,
    project: Annotated[
        bool,
        typer.Option(
            "--project/--no-project",
            help="Install into project-level settings (.claude/settings.json)",
        ),
    ] = False,
) -> None:
    """
    Add claude2sunfire-hook hooks to .claude/settings.json.

    By default writes to the user-level settings file
    (``~/.claude/settings.json``).  Pass ``--project`` to write to the
    project-level file (``.claude/settings.json`` in the current directory)
    instead.
    """
    targets: list[Path] = []
    if user:
        targets.append(Path.home() / ".claude" / "settings.json")
    if project:
        targets.append(Path.cwd() / ".claude" / "settings.json")

    if not targets:
        typer.echo("No target specified - use --user or --project.", err=True)
        raise typer.Exit(1)

    for settings_path in targets:
        _install_into(settings_path)
        typer.echo(f"✅ Hooks installed in {settings_path}")

    typer.echo(
        "\nRemember to configure your telemetry backend:\n"
        "  export OTEL_EXPORTER_OTLP_ENDPOINT='https://xxx:4318'\n"
        "  export OTEL_RESOURCE_ATTRIBUTES='service.name=claude-agents'\n"
    )


def _install_into(settings_path: Path) -> None:
    """Merge hook configuration into a settings.json file."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    for event_name, matchers in _HOOK_CONFIG.items():
        existing = hooks.setdefault(event_name, [])
        hook_cmd = matchers[0]["hooks"][0]["command"]

        # Skip if an identical command is already configured
        already_present = any(
            h.get("command") == hook_cmd
            for matcher in existing
            for h in matcher.get("hooks", [])
        )
        if not already_present:
            existing.extend(matchers)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Show-config command
# ---------------------------------------------------------------------------


@hook_app.command("show-config")
def cmd_show_config() -> None:
    """
    Print the JSON snippet to add to .claude/settings.json.

    Useful if you prefer to edit the file manually.
    """
    snippet = {"hooks": _HOOK_CONFIG}
    typer.echo(json.dumps(snippet, indent=2))


# ---------------------------------------------------------------------------
# Env-check command (convenience)
# ---------------------------------------------------------------------------


@hook_app.command("check-env")
def cmd_check_env() -> None:
    """
    Check that a telemetry backend is configured and print its status.
    """
    logfire_token = os.getenv("LOGFIRE_TOKEN")
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    debug_mode = os.getenv("CLAUDE_TELEMETRY_DEBUG")

    if logfire_token:
        typer.echo(f"🔥 Logfire token: ***{logfire_token[-4:]}")
    elif otel_endpoint:
        typer.echo(f"📊 OTEL endpoint: {otel_endpoint}")
        headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        if headers:
            typer.echo("   Headers: ***configured***")
    elif debug_mode:
        typer.echo("🔍 Debug mode active (console output only)")
    else:
        typer.echo(
            "❌ No telemetry backend configured.\n"
            "Set OTEL_EXPORTER_OTLP_ENDPOINT, LOGFIRE_TOKEN, or "
            "CLAUDE_TELEMETRY_DEBUG=1",
            err=True,
        )
        raise typer.Exit(1)
