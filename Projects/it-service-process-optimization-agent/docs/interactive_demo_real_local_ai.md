# Interactive demo: optional Real Local AI (Ollama)

The interactive LOCAL demo (`docs/interactive_demo.md`) can run its LangGraph investigation
with a real model served by a local Ollama instance. The deterministic mock stays the
default; the real model is used only when "Real Local AI (Ollama)" is selected.

```bash
scripts/run_interactive_demo.sh --port 8765                       # default model qwen3.5:9b
scripts/run_interactive_demo.sh --port 8765 --ollama-model glm-4.7-flash:latest
```

Then open http://127.0.0.1:8765/app/ → 2 · Investigate → 0. Choose the model.

## What changes and what does not

| Component | Real Local AI |
|---|---|
| LangGraph workflow, tools, budgets | Unchanged (same request defaults as the mock: 3 iterations, 6 tool calls) |
| Policy limits | Unchanged: 6 model calls/run, 10,000 input + 2,048 output tokens per call, 40,000 tokens/run, 240 s/run, 60 s per call, 0 retries |
| Validator, Evidence Contract, guardrails | Unchanged; policy 2.0.0 and the evidence pin of the served package are verified at startup |
| Human approval, tickets, audit | Unchanged; the browser calls the same `/workflow/*` routes |
| Execution label | `REAL_MODEL_OUTPUT` (mock: `DETERMINISTIC_MOCK_NOT_REAL_AI`) |

`OllamaLocalProvider` (`src/process_optimizer/agents/ollama_provider.py`) subclasses
`FoundryModelProvider` and replaces only the transport, so call caps, the token
reservation check, timeouts, error classification, Pydantic validation and invocation
records are inherited.

## Safety properties

- Endpoint must be `http` on a loopback literal; otherwise the app does not start.
  Environment proxies are ignored, redirects are not followed, and the launcher's socket
  guard still refuses non-loopback connections.
- Ollama cloud/remote models are refused; nothing is pulled or downloaded.
- No fallback: an unavailable model returns 503 `LOCAL_MODEL_UNAVAILABLE`, a concurrent run
  409 `LOCAL_MODEL_BUSY`, a reply from a different model 502 `MODEL_PROVENANCE_MISMATCH`.
- Malformed JSON, schema-invalid output, truncated replies (`done_reason != stop`) and
  context overflow are treated as `MODEL_OUTPUT_INVALID`.
- Text rewritten by a wording variant is labelled `modified_after_generation`.
- AI drafts use correlation IDs starting with `corr-ollama-`, so the audit trail shows them.

## Provider-local adaptations (documented, versioned)

1. Native `/api/chat` instead of `/v1`: the OpenAI-compatible route ignores `num_ctx`;
   the default 4,096-token context would silently truncate prompts. `num_ctx` is 16,384.
2. `maxLength` is capped at 1,000 in the model-facing schema (llama.cpp's grammar compiler
   rejects larger values). This only tightens; the full Pydantic model still decides.
3. Tool-request variants are narrowed to the stage allowlist that the prompt already
   states. The PRE_TOOL_CALL guardrail still checks every request (tested without grammar).
4. A short graph-protocol note (`ollama-graph-protocol-v1`) is appended to the unchanged
   phase5 system prompt for the investigator and optimizer roles.
5. `think: false`, `temperature: 0`.

## Historical model comparison (2026-11-02, Ollama 0.34.0, original BPI 2013 package, `group_returns`)

This comparison ran on the aggregate package built from the original dataset, which is not
included in this repository. It cannot be reproduced from the public synthetic package.

| Model | Outcome |
|---|---|
| qwen3.5:9b (default) | VALIDATED_RECOMMENDATIONS, reproducible, ~35–70 s |
| glm-4.7-flash | Full graph; recommendation blocked by validator (mis-cited numbers) |
| qwen2.5:14b-instruct | Did not follow the tool protocol (iteration/tool limits) |
| qwen3.5:27b, qwen3.8:27b-mlx | Final optimizer call exceeded the 60 s per-call timeout |
| Qwen3-Coder-30B-A3B | Exceeded the per-call input-token reservation |
| gemma4 | Disallowed tool / tool budget exhausted |

For `completed_lifecycle`, qwen3.5:9b currently ends in SAFE_TERMINATION (per-call token
reservation after large tool results); no recommendation is produced or submitted.

## Observed on the synthetic public package (2026-11-06, qwen3.5:9b, Ollama 0.34.0)

Five runs, all genuine model responses (`REAL_MODEL_OUTPUT`, 4 model calls each with 0 failed
attempts, loopback only):

| Scenario | Outcome |
|---|---|
| `group_returns` (3 runs, 40–50 s) | Recommendation produced, then blocked by the guardrails (`MISSING_SIMULATION_CITATION`); final decision `INSUFFICIENT_EVIDENCE`; nothing can be submitted |
| `completed_lifecycle` (2 runs, 24–30 s) | `SAFE_TERMINATION` (`TOOL_BUDGET_EXHAUSTED`); no recommendation |

So on the public package the real-model path demonstrates genuine local inference and
fail-closed guardrails, but no real-model recommendation reached human approval in these runs.
The complete approval flow is demonstrated with the deterministic provider.

## Known limitations

- Model output is probabilistic; only `group_returns` with qwen3.5:9b was observed to
  validate. Other scenarios and models end safely without a recommendation.
- `OPEN_NOT_SAFE` remains open: a plain "cause" claim is not blocked by policy 2.0.0,
  whether written by the mock variant or by a real model.
- The persisted approval record does not store model provenance; it is visible in the
  investigation response, the UI, and the `corr-ollama-` correlation ID only.
- Run summary states (completed / blocked / failed / not executed) are derived on the
  server from the graph's audit events and errors (`investigation_steps`); the page shows
  no step as completed unless the backend recorded it.
- Given a leading goal demanding causal proof and savings, qwen3.5:9b wrote a hedged,
  observational recommendation (validated). A genuinely unsafe, unmodified real-model
  output was observed with glm-4.7-flash (mis-cited numbers, blocked by the validator).
