"""Optional LOCAL model provider: Ollama on the loopback interface, never a cloud endpoint.

``OllamaLocalProvider`` subclasses ``FoundryModelProvider`` and replaces only the transport.
Every call still goes through the inherited ``_invoke``: the per-provider call cap, the
per-call token reservation check, the per-call timeout, sanitized error classification,
full Pydantic validation of the reply and the per-call invocation record. The LangGraph
workflow, the validator and the guardrail engine are unchanged and see the same typed
outputs they see from any provider.

Transport: Ollama's native ``POST /api/chat`` (not the OpenAI-compatible ``/v1`` route,
which ignores ``options.num_ctx`` and would silently truncate long prompts to the
server's default context). The JSON schema is passed as ``format``, so decoding is
grammar-constrained. llama.cpp's grammar compiler rejects ``maxLength`` above 1000, so
the model-facing copy caps string lengths at 1000; this only tightens the shape, and the
complete Pydantic model still decides.

Safety properties:

* the base URL must be ``http`` on a loopback literal (``127.0.0.1``, ``::1``,
  ``localhost``); proxies from the environment are ignored (``trust_env=False``) and
  redirects are not followed;
* Ollama "cloud" / remote models are refused, and nothing is ever pulled or downloaded;
* the model name the server reports for every reply is recorded, and a reply from a
  different model is treated as invalid output;
* a reply that hit the output cap or filled the context window is treated as invalid.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx

from process_optimizer.agents.provider import (
    FoundryModelProvider,
    ModelProviderError,
    effective_max_output_tokens,
)
from process_optimizer.guardrails.policy import load_policy

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
# Selected after probing the locally installed models (thinking disabled per request);
# see docs/interactive_demo_real_local_ai.md for the comparison and its limits.
DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"
PROVIDER_LABEL = "OLLAMA_LOCAL_LOOPBACK"
# Covers the policy's max_input_tokens_per_call (10000) plus max_output_tokens (2048);
# the inherited reservation check refuses any request that could exceed it.
OLLAMA_NUM_CTX = 16384
GRAMMAR_MAX_STRING_LENGTH = 1000
OLLAMA_KEEP_ALIVE = "10m"
# Provider-local description of the graph's control protocol, appended to the unchanged
# phase5 system prompt. It explains how the graph routes replies; it grants nothing, and
# every reply is still validated and guarded exactly as before.
LOCAL_PROTOCOL_VERSION = "ollama-graph-protocol-v1"
_GRAPH_PROTOCOL = {
    "InvestigatorOutput": (
        "Graph protocol: every entry in requested_tools is executed and you are called "
        "again with its result in prior_tool_records. When prior_tool_records already hold "
        "enough aggregate evidence, finish by returning requested_tools as an empty list "
        "and needs_more_analysis as false; the optimization stage then runs. Never repeat "
        "a tool call that already appears in prior_tool_records. Use only allowed_tools. "
        "Hypothesis statements must be hedged ('may', 'might', 'could') and non-causal."
    ),
    "OptimizationOutput": (
        "Graph protocol: either (a) request the simulation tool with needs_simulation true "
        "and recommendations empty, or (b) return exactly one recommendation with "
        "needs_simulation false and requested_tools empty. Choose (b) when tool_records "
        "already contain a successful simulation or when no group-transition intervention "
        "is proposed. evidence_ids must be finding_id values from selected_evidence. Each "
        "observed_numerical_fact must copy a value exactly from the cited finding, with "
        "field_path naming that field (for example 'measured_statistics.<name>' or "
        "'counts.<name>'), and its statement must state that same value. Do not write any "
        "other number in free text. Use only the simulation_id and assumption values of a "
        "successful simulation record, or set simulation to null."
    ),
}
_LOOPBACK_NAMES = {"localhost"}
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def loopback_base_url(value: str) -> str:
    """Return a normalized ``http://<loopback>:<port>`` URL or raise a configuration error."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        parts, port = None, None
    host = (parts.hostname or "") if parts else ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host in _LOOPBACK_NAMES
    if (
        parts is None
        or parts.scheme != "http"
        or not loopback
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ModelProviderError(
            "The Ollama endpoint must be plain http on a loopback address",
            category="PROVIDER_CONFIGURATION_ERROR",
        )
    netloc = f"[{host}]" if ":" in host else host
    return f"http://{netloc}:{port or 11434}"


def validate_model_name(value: str) -> str:
    """Accept a local model tag; refuse anything that names an Ollama cloud model."""
    name = value.strip()
    if not _MODEL_NAME.fullmatch(name) or "cloud" in name.lower():
        raise ModelProviderError(
            "The Ollama model must be a locally installed, non-cloud model tag",
            category="PROVIDER_CONFIGURATION_ERROR",
        )
    return name


def configured_ollama_model() -> str:
    return validate_model_name(os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL)


def configured_ollama_base_url() -> str:
    return loopback_base_url(os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL)


def _client(
    base_url: str, timeout_seconds: float, transport: httpx.BaseTransport | None
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        transport=transport,
        trust_env=False,  # never route loopback traffic through an environment proxy
        follow_redirects=False,
    )


def ollama_status(
    base_url: str,
    model: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Read-only availability check (``/api/version``, ``/api/tags``); never pulls a model."""
    status: dict[str, Any] = {
        "provider": PROVIDER_LABEL,
        "endpoint": base_url,
        "model": model,
        "available": False,
        "reason": None,
    }
    try:
        base_url = loopback_base_url(base_url)
        model = validate_model_name(model)
        with _client(base_url, timeout_seconds, transport) as client:
            version = client.get("/api/version")
            version.raise_for_status()
            tags = client.get("/api/tags")
            tags.raise_for_status()
            models = tags.json().get("models", [])
    except ModelProviderError:
        status["reason"] = "CONFIGURATION_REJECTED"
        return status
    except (httpx.HTTPError, ValueError, AttributeError):
        status["reason"] = "OLLAMA_UNREACHABLE"
        return status
    status["ollama_version"] = str(version.json().get("version", ""))[:32]
    entry = next(
        (item for item in models if isinstance(item, dict) and item.get("name") == model), None
    )
    if entry is None:
        status["reason"] = "MODEL_NOT_INSTALLED_LOCALLY"
        return status
    if any("remote" in key for key in entry):
        status["reason"] = "REMOTE_MODEL_REFUSED"
        return status
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, list) and "completion" not in capabilities:
        status["reason"] = "MODEL_LACKS_COMPLETION"
        return status
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    status.update(
        {
            "available": True,
            "digest": str(entry.get("digest", ""))[:64],
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "family": details.get("family"),
        }
    )
    return status


def grammar_safe_schema(node: Any) -> Any:
    """Cap ``maxLength`` for llama.cpp's grammar compiler; never relaxes a constraint."""
    if isinstance(node, list):
        return [grammar_safe_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    capped: dict[str, Any] = {}
    for key, value in node.items():
        if key == "maxLength" and isinstance(value, int):
            capped[key] = min(value, GRAMMAR_MAX_STRING_LENGTH)
        else:
            capped[key] = grammar_safe_schema(value)
    return capped


# The graph stage whose PRE_TOOL_CALL allowlist applies to each role's tool requests.
_SCHEMA_STAGES = {"InvestigatorOutput": "investigator", "OptimizationOutput": "optimizer"}


def stage_constrained_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Limit a role's tool-request variants to the policy allowlist of its graph stage.

    The prompt already states this allowlist; constrained decoding now honours it too.
    It only narrows what the model can emit: the PRE_TOOL_CALL guardrail still checks
    every request, and the full Pydantic model still validates every reply.
    """
    stage = _SCHEMA_STAGES.get(name)
    definitions = schema.get("$defs", {})
    variants = definitions.get("ToolRequest", {}).get("anyOf")
    if stage is None or not isinstance(variants, list):
        return schema
    allowed = {f"ToolRequest_{tool}" for tool in load_policy().stage_tools(stage)}
    kept = [item for item in variants if item.get("title") in allowed]
    if not kept:
        raise ModelProviderError(
            "No tool of this stage's allowlist is expressible in the response schema",
            category="PROVIDER_CONFIGURATION_ERROR",
        )
    return {**schema, "$defs": {**definitions, "ToolRequest": {"anyOf": kept}}}


class _OllamaStructuredRunnable:
    """Mimics the ``with_structured_output(..., include_raw=True)`` result contract."""

    def __init__(self, chat: _OllamaStructuredChat, response_format: dict[str, Any]) -> None:
        self._chat = chat
        name = str(response_format.get("name"))
        self._protocol = _GRAPH_PROTOCOL.get(name, "")
        self._schema = grammar_safe_schema(
            stage_constrained_schema(name, response_format["schema"])
        )

    def invoke(self, messages: list[tuple[str, str]], config: dict[str, Any] | None = None) -> Any:
        del config  # the client already carries the per-call timeout
        chat = self._chat
        schema_text = json.dumps(self._schema, separators=(",", ":"))
        payload = {
            "model": chat.model,
            "stream": False,
            "think": False,
            "format": self._schema,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": chat.max_output_tokens,
                "temperature": 0,
            },
            "messages": [
                {
                    "role": role,
                    "content": (
                        content
                        + ("\n" + self._protocol if self._protocol else "")
                        + "\nRespond with one JSON object that conforms to this JSON schema: "
                        + schema_text
                        if role == "system"
                        else content
                    ),
                }
                for role, content in messages
            ],
        }
        with _client(chat.base_url, chat.timeout_seconds, chat.transport) as client:
            response = client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        prompt_tokens = body.get("prompt_eval_count")
        output_tokens = body.get("eval_count")
        usage = None
        if isinstance(prompt_tokens, int) and isinstance(output_tokens, int):
            usage = {
                "input_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
            }
        reported_model = body.get("model") if isinstance(body.get("model"), str) else None
        raw = SimpleNamespace(
            usage_metadata=usage,
            response_metadata={
                "model_name": reported_model,
                "system_fingerprint": None,
                "done_reason": body.get("done_reason"),
                "total_duration_ns": body.get("total_duration"),
            },
        )
        parsed = None
        complete = (
            body.get("done") is True
            and body.get("done_reason") == "stop"
            and reported_model == chat.model
            and (usage is None or usage["total_tokens"] < OLLAMA_NUM_CTX)
        )
        content = (body.get("message") or {}).get("content")
        if complete and isinstance(content, str):
            try:
                candidate = json.loads(content)
            except json.JSONDecodeError:
                candidate = None
            parsed = candidate if isinstance(candidate, dict) else None
        return {"raw": raw, "parsed": parsed, "parsing_error": None}


class _OllamaStructuredChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.transport = transport

    def with_structured_output(
        self, response_format: dict[str, Any], *, method: str, include_raw: bool
    ) -> _OllamaStructuredRunnable:
        if method != "json_schema" or not include_raw:
            raise ModelProviderError(
                "Only raw-inclusive JSON-schema output is supported",
                category="PROVIDER_CONFIGURATION_ERROR",
            )
        return _OllamaStructuredRunnable(self, response_format)


class OllamaLocalProvider(FoundryModelProvider):
    """Real local inference through Ollama; labelled ``REAL_MODEL_OUTPUT`` by the graph."""

    mode = "real_model"

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        self.model_name = validate_model_name(model or configured_ollama_model())
        self.base_url = loopback_base_url(base_url or configured_ollama_base_url())
        self._transport = transport
        super().__init__(timeout_seconds=timeout_seconds, clock=clock, sleep=sleep)

    def _build_model(self, timeout_seconds: float) -> Any:  # type: ignore[override]
        return _OllamaStructuredChat(
            self.base_url,
            self.model_name,
            timeout_seconds,
            effective_max_output_tokens(),
            self._transport,
        )

    def _record_invocation(self, *args: Any, **kwargs: Any) -> None:
        super()._record_invocation(*args, **kwargs)
        record = self.invocation_records[-1]
        record["model_deployment"] = f"ollama:{self.model_name}"
        record["provider"] = PROVIDER_LABEL
        record["local_protocol_version"] = LOCAL_PROTOCOL_VERSION
