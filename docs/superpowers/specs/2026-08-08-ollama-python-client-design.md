# Ollama Python Client Design

## Goal

Use the official `ollama` Python library for Qwen fallback calls instead of manually constructing HTTP requests with `urllib`.

## Architecture

`OllamaQwenClient` remains the adapter consumed by `QwenFallbackService`. Internally it owns an `ollama.Client` instance and calls `client.chat(...)` with the existing Qwen model, system/user messages, deterministic options, `keep_alive="10m"`, and `format="json"`.

The constructor accepts an optional `host`. When a host is supplied it is passed directly to `ollama.Client(host=...)`. When omitted, no host argument is passed so the official library can use its normal `OLLAMA_HOST` behavior. `OLLAMA_MODEL` may still override the default `qwen3.5:2b` model.

## Error handling

- `ollama.ResponseError` becomes `LLMUnavailableError` and preserves the server error and HTTP status when present.
- connection failures become `LLMUnavailableError`.
- `ollama.RequestError` and malformed model output become `LLMResponseError`.
- output must still decode to exactly one JSON object.

## Testing

Tests inject a fake Ollama client/client factory so no real Ollama server is required. They verify explicit host forwarding, default host delegation to the library, chat parameters, JSON parsing, response errors, connection errors, and malformed output.
