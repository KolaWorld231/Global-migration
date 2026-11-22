"""LLM provider adapter. No real provider is bundled. Output from any provider is untrusted: the runtime validates
every tool call against the agent's allowlist, risk level and step budget before anything executes."""


class LLMProvider:
    name = "base"

    def complete(self, system: str, messages: list, tools: list[str]) -> dict:
        """Return {"tool": name, "args": {...}} to request a tool call, or {"final": <any>} to finish."""
        raise NotImplementedError


class NullProvider(LLMProvider):
    """Default. Agents run their deterministic handlers; no model is called."""
    name = "none"

    def complete(self, system, messages, tools):
        return {"final": "no model configured"}


class MockProvider(LLMProvider):
    """Scripted responses for tests."""
    name = "mock"

    def __init__(self, script):
        self.script = list(script)

    def complete(self, system, messages, tools):
        return self.script.pop(0) if self.script else {"final": "script exhausted"}


PROVIDERS = {"none": NullProvider}  # register real adapters here, e.g. PROVIDERS["claude"] = ClaudeProvider
