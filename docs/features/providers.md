# Providers (D9)

## Contract

`keyless.models.providers.base.Provider`:

```python
id, display_name, model, capabilities
status() -> ProviderStatus              # ready | needs_auth | not_installed | unreachable | model_missing
complete(system, user, *, json=False, images=()) -> str
warm()                                  # optional
close()
```

`ProviderCapabilities` declares text, JSON mode, image input, streaming, latency
class (`fast|slow`), and local. `ProviderStatus` never includes credentials.

## Adapters

| Adapter | File | Notes |
|---|---|---|
| `openai_compat` | `openai_compat.py` | One `httpx` client, `POST {base_url}/chat/completions`; presets in `presets.json`; images as `image_url` data parts; JSON via `response_format` |
| `ollama` | `ollama.py` | Native `/api/chat`, `/api/tags`, `/api/pull` progress, `/api/generate` warm |
| `acp` | `acp.py` | JSON-RPC over stdio to a spawned agent; one warm process; text only in the UI (slow) |
| ACP registry | `acp_registry.py` | Fetch `https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`; cache; offline fallback list |

Presets: OpenAI, OpenRouter, Groq, Together, Gemini's OpenAI-compatible endpoint,
LM Studio, llama.cpp, vLLM, Ollama `/v1`.

The native Gemini client was removed from `routing/llm.py`; `GEMINI_API_KEY` is
still read as the env fallback and `backend="gemini"` maps to the
`openai_compat` Gemini preset.

## Roles and consent

- `text_llm` needs text; JSON mode is optional.
- `gaze_vlm` needs image input, `latency_class=fast`, and **remote consent** if
  the provider is not local. Without consent the daemon disables the VLM and
  keeps DOM, OCR, and AT-SPI tiers.
- `ProviderBinding` is used by the daemon, settings, and onboarding to show why a
  provider is ineligible.

## ACP safety

- Spawned in a fresh empty temporary directory.
- `initialize` sends `clientCapabilities: {}`; no filesystem, terminal, or auth
  terminal capabilities are advertised.
- `session/request_permission` is answered with a reject option when offered,
  otherwise `cancelled`.
- Any `fs/*` or `terminal/*` agent request gets JSON-RPC `-32601`.
- The agent owns authentication. The app surfaces `needs_auth` and the agent's
  own login instructions; it stores no agent credentials.

## Secrets

`keyring` stores keys under service `keyless`, name = preset id. Env vars
(`OPENAI_API_KEY`, etc.) are the fallback. Keys never enter `settings.json`,
logs, or `doctor --json`.

## Verification

- Fake OpenAI-compatible HTTP server: status, auth failure, network failure, chat,
  JSON mode, and image parts.
- Fake HTTP Ollama server: tags/status, native chat, pull progress, warm.
- Fake ACP stdio agent: empty cwd, no client capabilities, permission rejection,
  filesystem request refusal, auth-required reporting, missing command.
- Real ACP round-trip: Claude Agent ACP (`@agentclientprotocol/claude-agent-acp`)
  returned "Hello." in about 1.5 s after a 1.3 s initialize.
- OpenCode `opencode acp` was tried; it initialized but its prompt reported
  `provider authentication required` despite stored CLI credentials. Cline
  `cline --acp` initialized but timed out on the prompt. These are recorded as
  agent-side limitations, not passing round-trips.
- No live hosted API call was made; no API key was used or stored by the test
  suite.

## CLI

```bash
uv run keyless providers presets
uv run keyless providers acp
uv run keyless providers status
uv run keyless providers set-key openai
uv run keyless providers delete-key openai
```

`providers status` calls each configured provider's `status()`; on a machine
without Ollama the text and gaze roles report `unreachable`, which is expected.
