"""Driving the agent judge with the Claude Agent SDK, and tracing its turns like any provider call.

The SDK runs the bundled Claude Code CLI as a subprocess inside the evaluation worker. Only the
judge's in-process MCP tools are offered: every built-in tool (Read, Grep, Bash, Write, ...) is
switched off, filesystem settings and CLAUDE.md files are not loaded, and the CLI runs in an
empty temporary directory. Requests authenticate with the research key passed explicitly, so a
legacy ``ANTHROPIC_API_KEY`` or a logged-in Claude account in the environment is never used.

``TracingAgent`` cannot see calls the CLI makes, so ``_SdkTraceRecorder`` writes one record per
model API call to the evaluation trace in the same shape: the call's new input (the opening prompt,
then the tool results since the previous call), the tool schemas, the reply, its final token usage
and latency. Cost and usage views therefore keep working unchanged.
"""

import asyncio
import tempfile
import time
from typing import Any, Final

import claude_agent_sdk as sdk

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.agents.models import estimate_cost_usd
from risk_bench.agents.tracing import TraceWriter
from risk_bench.runner.evaluation_agent import JudgeRun
from risk_bench.runner.evaluation_agent_tools import MAX_RESULT_CHARACTERS, JudgeToolDefinition, JudgeWorkspace

SERVER_NAME: Final[str] = "judge"
# belt and braces: `tools=[]` already removes the built-ins; these stay disallowed if a CLI default changes
_BUILT_IN_TOOLS: Final[tuple[str, ...]] = (
    "Agent",
    "Bash",
    "BashOutput",
    "Edit",
    "Glob",
    "Grep",
    "KillShell",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Skill",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
)
_RESULT_SIZE_HEADROOM: Final[int] = 20_000
_COMPETING_CREDENTIALS: Final[tuple[str, ...]] = (
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
)


class ClaudeAgentSdkLoop:
    """Runs the judge's tools through the Claude Agent SDK with turn and budget caps."""

    def __init__(
        self,
        model: str,
        effort: sdk.EffortLevel | None,
        api_key: str,
        max_turns: int,
        max_budget_usd: float,
        trace: TraceWriter | None,
        caller: str,
    ) -> None:
        self._model = model
        self._effort = effort
        self._api_key = api_key
        self._max_turns = max_turns
        self._max_budget_usd = max_budget_usd
        self._trace = trace
        self._caller = caller

    def run(self, workspace: JudgeWorkspace, instructions: str, prompt: str) -> JudgeRun:
        """Run the SDK query to completion inside this worker thread.

        Args:
            workspace: The trace and ledger the tools act on.
            instructions: The system prompt.
            prompt: The opening user message.

        Returns:
            How the query ended, with the SDK-reported and the trace-estimated cost.
        """
        return asyncio.run(self._run(workspace=workspace, instructions=instructions, prompt=prompt))

    async def _run(self, workspace: JudgeWorkspace, instructions: str, prompt: str) -> JudgeRun:
        definitions = workspace.tool_definitions()
        server = sdk.create_sdk_mcp_server(
            name=SERVER_NAME,
            version="1.0.0",
            tools=[_sdk_tool(workspace=workspace, definition=definition) for definition in definitions],
        )
        recorder = _SdkTraceRecorder(
            trace=self._trace,
            caller=self._caller,
            tools=[Tool(name=d.name, description=d.description, parameters=d.parameters) for d in definitions],
            opening=[Message(role="system", content=instructions), Message(role="user", content=prompt)],
        )
        result: sdk.ResultMessage | None = None
        with tempfile.TemporaryDirectory(prefix="risk-bench-judge-") as empty_directory:
            options = sdk.ClaudeAgentOptions(
                tools=[],
                allowed_tools=[_qualified_name(name=definition.name) for definition in definitions],
                disallowed_tools=list(_BUILT_IN_TOOLS),
                mcp_servers={SERVER_NAME: server},
                strict_mcp_config=True,
                setting_sources=[],
                permission_mode="dontAsk",
                system_prompt=instructions,
                model=self._model,
                effort=self._effort,
                max_turns=self._max_turns,
                max_budget_usd=self._max_budget_usd,
                # final per-call usage (output and thinking tokens) arrives only as stream events
                include_partial_messages=True,
                cwd=empty_directory,
                env=_research_credentials(api_key=self._api_key),
            )
            try:
                async for message in sdk.query(prompt=prompt, options=options):
                    recorder.observe(message=message)
                    if isinstance(message, sdk.ResultMessage):
                        result = message
            except Exception:
                # after an error result (turn or budget cap) the CLI exits non-zero on purpose and the
                # SDK raises; the result already says why, so only an error without one propagates
                if result is None or not result.is_error:
                    raise
            finally:
                recorder.flush()
        if result is None:
            return JudgeRun(
                turns=recorder.turns,
                stop_reason="no_result",
                estimated_cost_usd=recorder.estimated_cost_usd,
                reported_cost_usd=None,
                error="The Agent SDK ended without a result message.",
            )
        failed = result.is_error or result.subtype != "success"
        return JudgeRun(
            turns=result.num_turns,
            stop_reason=result.subtype,
            estimated_cost_usd=recorder.estimated_cost_usd,
            reported_cost_usd=result.total_cost_usd,
            error=("; ".join(result.errors or []) or result.result) if failed else None,
        )


class _SdkTraceRecorder:
    """Folds the SDK's message stream into one trace record per model API call.

    The CLI streams one reply as several assistant messages, one per content block, and when a reply
    makes parallel tool calls it interleaves each call's result between them. Records are therefore
    grouped by API message id, never split at a tool result, and every result belongs to the input of
    the next call. The usage on an assistant message is the snapshot from the start of the stream (a
    handful of output tokens, no thinking); the final usage comes from the ``message_delta`` stream
    event, so the loop enables partial messages and the snapshot is only a fallback.
    """

    def __init__(self, trace: TraceWriter | None, caller: str, tools: list[Tool], opening: list[Message]) -> None:
        self._trace = trace
        self._caller = caller
        self._tools = tools
        self._pending_input: list[Message] = list(opening)
        self._turn_input: list[Message] = []
        self._turn_id: str | None = None
        self._turn_messages: list[sdk.AssistantMessage] = []
        self._request_sent = time.monotonic()
        self._turn_requested = self._request_sent
        self._turn_answered = self._request_sent
        self._streamed_id: str | None = None
        self._streamed_usage: dict[str, dict[str, Any]] = {}
        self.turns = 0
        self.estimated_cost_usd: float | None = 0.0

    def observe(self, message: object) -> None:
        if isinstance(message, sdk.StreamEvent):
            self._observe_stream(event=message.event)
            return
        if isinstance(message, sdk.AssistantMessage):
            turn_id = message.message_id or message.uuid
            if self._turn_messages and turn_id != self._turn_id:
                self.flush()
            if not self._turn_messages:
                self._turn_id = turn_id
                self._turn_input, self._pending_input = self._pending_input, []
                self._turn_requested = self._request_sent
            self._turn_messages.append(message)
            self._turn_answered = time.monotonic()
            return
        if isinstance(message, sdk.UserMessage):
            self._pending_input.extend(_tool_result_messages(message=message))
            self._request_sent = time.monotonic()

    def _observe_stream(self, event: dict[str, Any]) -> None:
        if event.get("type") == "message_start":
            started = event.get("message") or {}
            self._streamed_id = started.get("id")
            if self._streamed_id:
                self._streamed_usage[self._streamed_id] = dict(started.get("usage") or {})
        elif event.get("type") == "message_delta" and self._streamed_id in self._streamed_usage:
            final = {key: value for key, value in (event.get("usage") or {}).items() if value is not None}
            self._streamed_usage[self._streamed_id].update(final)

    def flush(self) -> None:
        if not self._turn_messages:
            return
        messages, self._turn_messages = self._turn_messages, []
        blocks = [block for message in messages for block in message.content]
        snapshot = next((m.usage for m in reversed(messages) if m.usage), None) or {}
        streamed = self._streamed_usage.pop(self._turn_id, None) if self._turn_id else None
        usage = streamed or snapshot
        cache_write = usage.get("cache_creation_input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        response = Response(
            text="".join(block.text for block in blocks if isinstance(block, sdk.TextBlock)) or None,
            tool_calls=tuple(
                ToolCall(id=block.id, name=_unqualified_name(name=block.name), arguments=block.input)
                for block in blocks
                if isinstance(block, sdk.ToolUseBlock)
            ),
            input_tokens=(usage.get("input_tokens") or 0) + cache_write + cache_read,
            output_tokens=usage.get("output_tokens") or 0,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
            stop_reason=messages[-1].stop_reason,
        )
        model = messages[-1].model
        cost = estimate_cost_usd(
            model=model,
            input_tokens=response.input_tokens or 0,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
            output_tokens=response.output_tokens or 0,
        )
        self.estimated_cost_usd = (
            None if self.estimated_cost_usd is None or cost is None else (self.estimated_cost_usd + cost)
        )
        self.turns += 1
        error = next((m.error for m in reversed(messages) if m.error), None)
        if self._trace is not None:
            self._trace.write_call(
                caller=self._caller,
                model=model,
                messages=self._turn_input,
                tools=self._tools,
                response=response,
                error=str(error) if error else None,
                latency_ms=int((self._turn_answered - self._turn_requested) * 1000),
            )
        self._turn_input = []


def _sdk_tool(workspace: JudgeWorkspace, definition: JudgeToolDefinition) -> sdk.SdkMcpTool[Any]:
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        outcome = workspace.call(name=definition.name, arguments=arguments)
        return {"content": [{"type": "text", "text": outcome.text}], "is_error": outcome.is_error}

    # results stay inline: with the built-in tools off, the agent could not open a persisted file
    annotations = sdk.ToolAnnotations(maxResultSizeChars=MAX_RESULT_CHARACTERS + _RESULT_SIZE_HEADROOM)
    return sdk.tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.parameters,
        annotations=annotations,
    )(handler)


def _research_credentials(api_key: str) -> dict[str, str]:
    # The CLI inherits the worker's environment, and an auth token, OAuth login, cloud-provider
    # switch or host-managed provider (set when the server runs inside another Claude Code session)
    # would all win over an API key. Blank them so only the research key authenticates; it also
    # replaces any inherited legacy ANTHROPIC_API_KEY.
    return {"ANTHROPIC_API_KEY": api_key} | dict.fromkeys(_COMPETING_CREDENTIALS, "")


def _tool_result_messages(message: sdk.UserMessage) -> list[Message]:
    if isinstance(message.content, str):
        return [Message(role="user", content=message.content)]
    return [
        Message(role="tool", content=_tool_result_text(content=block.content), tool_call_id=block.tool_use_id)
        for block in message.content
        if isinstance(block, sdk.ToolResultBlock)
    ]


def _tool_result_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None or isinstance(content, str):
        return content or ""
    return "".join(str(part.get("text", "")) for part in content)


def _qualified_name(name: str) -> str:
    return f"mcp__{SERVER_NAME}__{name}"


def _unqualified_name(name: str) -> str:
    return name.removeprefix(f"mcp__{SERVER_NAME}__")
