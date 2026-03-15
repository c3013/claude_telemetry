# Observability for Claude Code

[![CI](https://github.com/TechNickAI/claude_telemetry/actions/workflows/ci.yml/badge.svg)](https://github.com/TechNickAI/claude_telemetry/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

`claude_telemetry` wraps the Claude Code CLI with full observability. Every tool call,
token count, and cost flows to your OTEL backend as structured traces.

## The Problem

Claude Code works beautifully in your terminal. But when you run agents headless—in
CI/CD, cron jobs, production, or remote servers—you lose visibility into what's
happening. You can't see which tools were called, don't know token usage or costs, have
no context when debugging failures, and can't optimize without re-running locally.

Headless environments lack the rich console output you get during development.

## The Solution

`claude_telemetry` is a thin wrapper around the Claude Code CLI that adds observability.
It passes through all Claude Code flags unchanged while capturing execution traces.
Works with any OTEL backend—Logfire, Datadog, Honeycomb, Grafana. See exactly what
happened in production, debug remote failures without local reproduction, and track
costs to optimize expensive workflows.

Your agents become observable whether local or headless.

## Quick Start

### Option 1: Hook-based (recommended) — keep using `claude` as-is

Add OTel tracing to your existing `claude` workflow without changing any commands.
Just configure hooks in `~/.claude/settings.json` once and every `claude` session
is automatically traced.

**Step 1 – Install:**

```bash
pip install claude_telemetry
```

**Step 2 – Configure a telemetry backend (one of):**

```bash
# Any OTEL backend (Honeycomb, Datadog, Grafana, …)
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.honeycomb.io"
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<key>"

# Or Logfire
export LOGFIRE_TOKEN="<token>"

# Or console output (useful for debugging)
export CLAUDE_TELEMETRY_DEBUG=1
```

**Step 3 – Install hooks into `~/.claude/settings.json`:**

```bash
claude-telemetry-hook install
```

Or if you prefer to edit the file manually, run `claude-telemetry-hook show-config`
for the exact JSON to add.

**Step 4 – Use `claude` as normal:**

```bash
claude "Analyze my project and suggest improvements"
```

Traces appear in your OTel backend automatically after each session ends.
No wrapper, no code changes—just hooks that fire in the background.

---

### Option 2: `claudia` wrapper — drop-in replacement for `claude`

The simplest way to add observability: swap `claude` for `claudia` on the command line.

```bash
# Before
claude code "Analyze my project and suggest improvements"

# After - same command, now with observability
claudia "Analyze my project and suggest improvements"
```

That's it. Every flag you use with `claude code` works with `claudia`. The behavior is
identical, but now you get full traces in your observability platform.

## `.claude/settings.json` Hooks (Option 1 details)

The `claude-telemetry-hook` CLI provides one sub-command per Claude Code hook event.
Claude Code invokes each command as a subprocess, passing JSON on stdin.

### Available commands

| Command | Claude Code event | What it does |
|---|---|---|
| `user-prompt-submit` | `UserPromptSubmit` | Initialises session state file |
| `pre-tool-use` | `PreToolUse` | Records tool start + input |
| `post-tool-use` | `PostToolUse` | Records tool result |
| `message-complete` | `MessageComplete` | Accumulates token counts |
| `pre-compact` | `PreCompact` | Records context-compaction event |
| `stop` | `Stop` | Exports complete OTel trace & cleans up |

### Manual configuration

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook user-prompt-submit"}]}
    ],
    "PreToolUse": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook pre-tool-use"}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook post-tool-use"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook stop"}]}
    ]
  }
}
```

You can also add `MessageComplete` and `PreCompact` hooks for richer token-count and
compaction events:

```json
    "MessageComplete": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook message-complete"}]}
    ],
    "PreCompact": [
      {"hooks": [{"type": "command", "command": "claude-telemetry-hook pre-compact"}]}
    ]
```

### How the hook pipeline works

Each data-collection hook (`user-prompt-submit`, `pre-tool-use`, etc.) writes event
records to a session state file at `~/.cache/claude_telemetry/sessions/<session_id>.json`.
The file persists across the multiple subprocess invocations that make up a single
Claude Code session.

When Claude Code fires the `Stop` event, `claude-telemetry-hook stop` reads the
complete state, creates a single OTel session span containing all events in
chronological order, exports it to the configured backend, and removes the state file.

This approach avoids the "long-lived span across processes" problem: you get one
clean, complete trace per session with accurate timings.

### Utility commands

```bash
# Print the JSON snippet (for manual editing)
claude-telemetry-hook show-config

# Check that a backend is configured
claude-telemetry-hook check-env

# Install into project-level settings instead of user-level
claude-telemetry-hook install --no-user --project
```

## For Developers

If you're already using `claude code` in your workflow, switching to `claudia` gives you
instant observability with zero behavior changes.

**Command-line usage:**

```bash
# Your existing workflow
claude code --model opus "Refactor this module"

# Same command, now observable
claudia --model opus "Refactor this module"
```

**In scripts and automation:**

```bash
#!/bin/bash
# CI/CD, cron jobs, or automation scripts

# Before - no visibility into what happened
claude code "Run tests and fix any failures"

# After - full traces in your observability platform
claudia "Run tests and fix any failures"
```

**The result:**

When you use `claudia`, you get the exact same output in your terminal as `claude code`.
But now your observability platform shows every tool call, token count, cost, and
timing. Debug headless failures, track production costs, and optimize expensive
workflows—all without changing how you work.

### Installation

```bash
# Basic installation - works with any OTEL backend
pip install claude_telemetry

# Or with Logfire support for enhanced LLM telemetry
pip install "claude_telemetry[logfire]"

# Or with Sentry for LLM monitoring with error tracking
pip install claude_telemetry sentry-sdk
```

### For Python Scripts

Add one line to your code:

```python
from claude_telemetry import run_agent_with_telemetry

# Instead of using Claude SDK directly, use this wrapper:
await run_agent_with_telemetry(
    prompt="Analyze my project and suggest improvements",
)
```

### Configure Your Backend

Same configuration for CLI and Python:

```bash
# For Logfire (get token from logfire.pydantic.dev)
export LOGFIRE_TOKEN="your-token"

# For Sentry (get DSN from sentry.io)
export SENTRY_DSN="https://your-key@sentry.io/project-id"

# Or for any OTEL backend
export OTEL_EXPORTER_OTLP_ENDPOINT="https://your-otel-endpoint.com"
export OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer your-token"
```

That's it. Your agent's telemetry is now flowing to your observability platform.

## Usage Examples

### Headless/Production Use Case

The main use case: running agents in environments where you can't see console output.

```python
# In your CI/CD, cron job, or production script:
from claude_telemetry import run_agent_with_telemetry

await run_agent_with_telemetry(
    prompt="Analyze the latest logs and create a report",
    extra_args={"model": "sonnet"},
)
```

Your observability dashboard shows which tools were called, what errors occurred, how
many tokens were used, and the total cost of the operation.

### Local Development with Visibility

Even during local development, seeing traces helps you understand agent behavior:

```python
from claude_telemetry import run_agent_with_telemetry

# Your normal Claude Code workflow, now with observability
await run_agent_with_telemetry(
    prompt="Refactor the authentication module",
)
```

**With any OTEL backend:**

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from claude_telemetry import run_agent_with_telemetry

# Configure your OTEL backend
provider = TracerProvider()
processor = BatchSpanProcessor(
    OTLPSpanExporter(
        endpoint="https://api.honeycomb.io/v1/traces",
        headers={"x-honeycomb-team": "your_api_key"},
    )
)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

# Run with telemetry
await run_agent_with_telemetry(
    prompt="Your task here",
)
```

### CLI Usage

The `claudia` CLI passes all Claude Code flags through unchanged while adding
observability:

```bash
# Basic usage - just like `claude code`
claudia "Analyze my recent emails and create a summary"

# Pass any Claude Code flag
claudia --model opus "Refactor this module"
claudia --permission-mode bypassPermissions "Run the tests"
claudia --debug "Why is this failing?"

# Combine multiple flags - all forward to Claude Code
claudia --model haiku --permission-mode bypassPermissions "Quick task"
```

The `claudia` command accepts the same flags as `claude code`. It wraps the CLI with
telemetry hooks that capture execution without changing behavior. Configure your
observability backend via environment variables (same as library usage).

## What Gets Captured

Every agent run creates a full trace showing exactly what happened:

**Per execution:**

- 📝 Prompt and system instructions
- 🤖 Model used
- 🔢 Token counts (input/output/total)
- 💰 Cost in USD
- 🔧 Number of tool calls
- ⏱️ Execution time
- ❌ Any errors or failures

**Per tool call:**

- Tool name (Read, Write, Bash, etc.)
- Tool inputs
- Tool outputs
- Individual execution time
- Success/failure status

This gives you complete visibility into what your agent did, why it failed, and how much
it cost.

## Span Hierarchy

```
claude.agent.run (parent span)
  ├─ user.prompt (event)
  ├─ tool.read (child span)
  │   ├─ tool.input (attribute)
  │   └─ tool.output (attribute)
  ├─ tool.write (child span)
  │   ├─ tool.input (attribute)
  │   └─ tool.output (attribute)
  └─ agent.completed (event)
```

## Backend-Specific Features

### Logfire

When using Logfire, the package enables LLM-specific UI features. Spans tagged with
`LLM` show in Logfire's LLM UI with request/response formatted for token visualization
and tool calls displayed as structured data. Enhanced formatting includes emoji
indicators (🤖 for agents, 🔧 for tools, ✅ for completion), proper nesting in console
output, and readable span titles showing task descriptions instead of generic "Message
with model X" text.

This happens automatically when `LOGFIRE_TOKEN` is set.

### Sentry

When using Sentry, the package integrates with Sentry's AI Monitoring features. Spans
include `gen_ai.*` attributes that appear in Sentry's AI Performance dashboard. You get
full LLM trace visualization showing token usage, costs, model performance, and tool
execution alongside Sentry's error tracking and performance monitoring.

Key features:

- **AI Performance Dashboard**: View LLM metrics by model, operation, and pipeline
- **Token Usage Tracking**: Monitor input/output tokens with cost analysis
- **Error Context**: LLM errors include full trace context (prompt, model, tokens)
- **Tool Execution Visibility**: See which tools were called and their results

Set `SENTRY_DSN` and optionally configure:

```bash
export SENTRY_ENVIRONMENT="production"        # Environment name
export SENTRY_TRACES_SAMPLE_RATE="1.0"        # Trace sampling (0.0-1.0)
```

View traces at: Sentry Dashboard → Performance → AI Monitoring

With other backends, you get standard OTEL spans.

## Configuration

### Environment Variables

**Logfire:**

```bash
export LOGFIRE_TOKEN="your_token"  # Get from logfire.pydantic.dev
```

**Sentry:**

```bash
export SENTRY_DSN="https://your-key@sentry.io/project-id"
export SENTRY_ENVIRONMENT="production"           # Optional
export SENTRY_TRACES_SAMPLE_RATE="1.0"          # Optional (0.0-1.0)
```

**Any OTEL backend:**

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://your-endpoint.com/v1/traces"
export OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer your-token"
export OTEL_SERVICE_NAME="my-claude-agents"  # Optional, defaults to "claude-agents"
```

**Debug mode:**

```bash
export OTEL_DEBUG=1  # Verbose telemetry logging
```

### Programmatic Configuration

For more control, configure the tracer provider yourself:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from claude_telemetry import run_agent_with_telemetry

provider = TracerProvider()
# ... add your processors ...
trace.set_tracer_provider(provider)

# Pass it to the runner
await run_agent_with_telemetry(
    prompt="Your task",
    tracer_provider=provider,
)
```

### MCP Servers

MCP servers work the same way as with `claude code`. Configure them using
`claude mcp add`:

```bash
# Add an MCP server using Claude's config system
claude mcp add mcp-hubby --url https://connect.mcphubby.ai/mcp

# Or use project-local .mcp.json (same format as Claude Code)
# The wrapper loads user, project, and local settings automatically
```

The wrapper passes `setting_sources=["user", "project", "local"]` to the SDK, which
means it loads MCP servers from the same places `claude code` does. No special
configuration needed—if it works with `claude code`, it works with `claudia`.

**Need help with MCP servers?** Check out [MCP Hubby](https://mcphubby.ai)—a single
gateway to all your services (Gmail, Notion, Slack, etc.) that reduces context usage by
95%. One MCP connection instead of dozens.

## API

```python
async def run_agent_with_telemetry(
    prompt: str,
    extra_args: dict[str, str | None] | None = None,
    tracer_provider: TracerProvider | None = None,
    debug: bool = False,
)
```

**Parameters:**

- `prompt` - Task for Claude Code
- `extra_args` - Claude Code CLI flags as a dictionary. Any flag you can pass to
  `claude code` works here (e.g.,
  `{"model": "opus", "permission-mode": "bypassPermissions"}`)
- `tracer_provider` - Custom OTEL tracer provider (optional, auto-detected if not
  provided)
- `debug` - Enable Claude CLI debug mode

**Returns:**

- Nothing directly. Prints Claude's responses to console and sends all telemetry via
  OTEL.

**How it works:**

The function wraps the Claude Code SDK with observability hooks. It converts
`extra_args` to CLI flags and passes them through unchanged. The SDK runs exactly as if
you called `claude code` directly, but telemetry hooks capture every event.

**Example:**

```python
import asyncio
from claude_telemetry import run_agent_with_telemetry

async def main():
    # Any flag from `claude code --help` works in extra_args
    await run_agent_with_telemetry(
        prompt="List Python files and create a summary",
        extra_args={"model": "sonnet", "permission-mode": "bypassPermissions"},
    )

asyncio.run(main())
```

## How It Works

`claude_telemetry` is a thin observability layer around the Claude Code SDK. It uses the
SDK's hook system to capture execution without modifying behavior.

**Pass-through architecture:**

The `extra_args` dictionary passes directly to the Claude Code SDK as CLI flags. When
you call `run_agent_with_telemetry(prompt="...", extra_args={"model": "opus"})`, the SDK
receives exactly what `claude code --model opus` would pass. The library doesn't
interpret or validate flags—it forwards them unchanged. This means any flag that works
with `claude code` works here, including future flags not yet released.

**Observability hooks:**

- `UserPromptSubmit` - Opens parent span, logs prompt
- `PreToolUse` - Opens child span for tool, captures input
- `PostToolUse` - Captures output, closes tool span
- Session completion - Adds final metrics, closes parent span

Hooks are async callbacks that run during SDK execution. They capture telemetry data
without blocking or modifying the agent's behavior.

**OTEL export:**

- Spans sent via configured OTEL exporter
- Attributes follow semantic conventions where applicable
- Events add context without creating spans
- Works with any OTEL-compatible backend

**Logfire detection:**

- Checks for `LOGFIRE_TOKEN` environment variable
- If present, uses Logfire's Python SDK for auto-config
- Adds LLM-specific formatting and tags
- Falls back to standard OTEL if token not found

## Architecture Decisions

### Why Pass-Through Instead of Parameters?

Earlier versions exposed individual parameters like `model`, `allowed_tools`, etc. This
created maintenance burden—every new Claude Code flag required updating the wrapper's
signature. The pass-through architecture using `extra_args` eliminates this problem.

When you pass `extra_args={"model": "opus"}`, the library converts it to `--model opus`
and forwards it to the SDK unchanged. The library doesn't know what flags are valid—it
trusts the SDK to handle them. This means new Claude Code features work immediately
without updating `claude_telemetry`.

The CLI uses plain argument parsing to separate observability flags (`--logfire-token`,
`--otel-endpoint`) from Claude Code flags. Everything else passes through. This keeps
the wrapper thin and maintainable.

### Why OpenTelemetry?

OpenTelemetry is the industry standard for observability. Using it means the package
works with any observability backend, doesn't lock users into specific vendors,
integrates with existing infrastructure, and is future-proof as a CNCF project with wide
adoption.

### Why Special-Case Logfire and Sentry?

Both Logfire and Sentry have LLM-specific UI features that benefit from specific span
formatting. When detected:

- **Logfire**: Uses Logfire's SDK for auto-configuration and LLM UI formatting
- **Sentry**: Adds `gen_ai.*` attributes for AI Performance dashboard integration

Both use OpenTelemetry under the hood, so the hook code stays backend-agnostic. The
adapters just configure the provider and ensure proper attribute formatting for each
platform's LLM UI features.

### Why Hooks Instead of Wrappers?

The Claude SDK provides hooks specifically for observability. Using them captures all
events without modifying SDK code, works across SDK updates, maintains clean separation
of concerns, and requires no monkey-patching.

## Supported Backends

**Tested and working:**

- Logfire (enhanced LLM features)
- Sentry (AI monitoring with error tracking)
- Honeycomb
- Datadog
- Grafana Cloud
- Self-hosted OTEL collector

**Should work (standard OTEL):**

- New Relic
- Elastic APM
- AWS X-Ray
- Azure Monitor
- Any OTLP-compatible endpoint

## Console Output

Regardless of backend, console shows execution:

```
🤖 Analyze my recent emails and summarize them
  👤 User prompt submitted
  🔧 Calling tool: Read
  ✅ Tool completed: Read
  🔧 Calling tool: Write
  ✅ Tool completed: Write
  🎉 Agent completed

Session completed - Tokens: 145 in, 423 out, Tools called: 2
```

## Requirements

- Python 3.10 or later
- `claude-agent-sdk` - Claude Code integration
- `opentelemetry-api` - OTEL core
- `opentelemetry-sdk` - OTEL implementation
- `opentelemetry-exporter-otlp` - OTLP export
- `logfire` (optional) - Enhanced Logfire features

## Development

```bash
git clone https://github.com/TechNickAI/claude_telemetry.git
cd claude_telemetry
pip install -e ".[dev]"

# Run tests
pytest

# Run example with Logfire
export LOGFIRE_TOKEN="your_token"
python examples/logfire_example.py

# Run example with Honeycomb
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.honeycomb.io"
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=your_key"
python examples/otel_example.py
```

## Project Structure

```
claude_telemetry/
  claude_telemetry/
    __init__.py           # Package exports
    runner.py             # Async agent runner with observability hooks
    sync.py               # Sync wrapper for runner
    hooks.py              # Telemetry hook implementations
    telemetry.py          # OTEL configuration and setup
    logfire_adapter.py    # Logfire-specific enhancements
    sentry_adapter.py     # Sentry-specific configuration
    cli.py                # CLI entry point with pass-through arg parsing
    helpers/              # Logger and utilities
  examples/
    logfire_example.py    # Logfire usage
    sentry_example.py     # Sentry LLM monitoring
    otel_example.py       # Generic OTEL usage
    honeycomb_example.py  # Honeycomb setup
  tests/
    test_telemetry.py     # Core telemetry tests
    test_cli_parsing.py   # CLI argument pass-through tests
    test_hooks.py         # Hook behavior tests
  pyproject.toml          # Package config
  README.md
  LICENSE
```

## Implementation Notes

### Logfire LLM Formatting

When Logfire is detected, spans need specific attributes for LLM UI:

```python
# Standard OTEL span
span.set_attribute("prompt", "...")
span.set_attribute("model", "...")

# Logfire LLM enhancement
span.set_attribute("request_data", {
    "model": "sonnet",
    "messages": [{"role": "user", "content": "..."}]
})
span.set_attribute("response_data", {
    "message": {"role": "assistant", "content": "..."},
    "usage": {"input_tokens": 123, "output_tokens": 456}
})
```

Logfire's UI parses these attributes to show token flow and LLM-specific visualizations.

### Hook Implementation

Hooks must be async and match the signature:

```python
async def on_user_prompt_submit(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext
) -> HookJSONOutput:
    # Open parent span
    # Log user prompt
    return {}  # Can return data to modify flow
```

Register hooks in SDK options:

```python
options = ClaudeAgentOptions(
    hooks={
        "UserPromptSubmit": [HookMatcher(matcher=None, hooks=[on_prompt])],
        "PreToolUse": [HookMatcher(matcher=None, hooks=[on_pre_tool])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[on_post_tool])],
    }
)
```

## FAQ & Troubleshooting

### Does `claudia` support all Claude Code flags?

Yes. The pass-through architecture means any flag that works with `claude code` works
with `claudia`. We don't maintain a list of supported flags—we just forward everything
to the SDK unchanged. This includes flags that don't exist yet. When Anthropic releases
new Claude Code features, they work immediately without updating `claude_telemetry`.

### Why not just use Logfire directly?

You can! But `claude_telemetry` works with any OTEL backend (not just Logfire), provides
pre-configured hooks for Claude agents specifically, captures LLM-specific metrics like
tokens, costs, and tool calls, and saves you setup time—no need to instrument everything
manually.

Use this if you want observability with minimal code changes.

### Does this add latency?

Negligible. Telemetry is async and doesn't block agent execution. Typical overhead:
<10ms per operation.

### What about streaming responses?

Fully supported! Streaming responses are captured and sent to telemetry after
completion.

### Common Issues

**No traces appearing:**

- Check your OTEL endpoint is reachable
- Verify environment variables are set
- Check console for error messages about telemetry connection

**Logfire LLM UI not showing:**

- Ensure `LOGFIRE_TOKEN` is set
- Install the `logfire` extra: `pip install "claude_telemetry[logfire]"`
- Check console for "Logfire project URL" to confirm connection

**Agent runs but no telemetry:**

- Make sure you're using `run_agent_with_telemetry()` wrapper
- Check that backend environment variables are set
- Try setting `OTEL_DEBUG=1` for verbose logging

**High costs showing in traces:**

- This is valuable data! Use it to optimize your prompts
- Consider using `haiku` model for cheaper operations
- Review which tools are being called unnecessarily

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

Built for the 100x community.

Package name: `claude_telemetry` CLI name: `claudia`

Based on OpenTelemetry standards. Enhanced Logfire integration when available.
