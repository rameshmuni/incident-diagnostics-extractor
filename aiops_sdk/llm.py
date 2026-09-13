"""
The pluggable LLM layer. Every other piece of this SDK talks to an
LLMProvider's .generate(prompt) -> str and never imports a vendor SDK or
calls an API directly - swapping models means handing AiopsSDK a different
provider instance; nothing else in this package, or in a consumer's code,
has to change.

GeminiProvider does not reimplement any model-calling logic. It is a thin
pass-through to query_incident_gemini.call_gemini() - the exact, unmodified
function app.py already calls in production. The SDK doesn't duplicate that
function, it just gives it a stable name (.generate) other code can depend
on instead of importing call_gemini by name.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """The one contract every model backend must satisfy: text in, text out.

    Anyone adding a new provider (OpenAI, Claude, a local model, ...) only
    needs to subclass this and implement generate(). Every other class in
    this SDK (AiopsSDK, IncidentSearch's callers) only ever calls
    provider.generate(prompt) - it has no idea which subclass it's holding.
    """

    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    """Wraps this project's existing Gemini call - see query_incident_gemini.py.

    Deliberately does not import google.genai itself, cache a client, or
    touch GEMINI_API_KEY directly - all of that stays exactly where it
    already lives, in query_incident_gemini.get_client()/call_gemini(). This
    class only exists so callers depend on a stable .generate() method
    instead of importing call_gemini by name (see the SDK fundamentals
    write-up for why that indirection is the entire point).
    """

    def __init__(self, model=None):
        # imported lazily, inside __init__, not at module load time - so
        # merely importing aiops_sdk never requires GEMINI_API_KEY to be
        # set. The key is only needed the first time .generate() actually
        # runs, exactly as it already works in query_incident_gemini.py.
        from query_incident_gemini import GEMINI_MODEL

        self._model = model or GEMINI_MODEL

    def generate(self, prompt: str) -> str:
        # looked up fresh on every call, not cached on self at construction
        # time - a long-lived GeminiProvider (e.g. one built once at app
        # startup, as aiops_sdk_demo_app/app.py does) must still call
        # whatever query_incident_gemini.call_gemini is at the moment
        # generate() runs, not whatever it was when the provider was built.
        from query_incident_gemini import call_gemini

        return call_gemini(prompt, model=self._model)

    @property
    def model(self) -> str:
        """The model name this provider generates with - for display in a
        UI's status strip, e.g. app.py's /api/status. Not a network call."""
        return self._model

    def __repr__(self):
        return f"GeminiProvider(model={self._model!r})"
