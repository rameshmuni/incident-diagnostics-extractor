"""
aiops_sdk - the reusable core behind the AIOps Virtual Agent, packaged so any
app can `import aiops_sdk` instead of copying scripts out of this project.

This package is purely additive. Every class below is a thin wrapper around
functions that already exist elsewhere in this project (query_incident.py,
query_incident_gemini.py, seed_resolved_incidents.py,
week3/auto_remediation.py) - nothing in app.py, week3/, or any file outside
this folder was changed to build it. See README.md in this folder for what's
wrapped, why, and what adopting it in app.py would actually look like.

    from aiops_sdk import AiopsSDK

    sdk = AiopsSDK()                       # Gemini by default
    result = sdk.auto_remediate(incident_text)

Swapping the LLM later means constructing AiopsSDK(llm_provider=...) with a
different LLMProvider - see llm.py.
"""

from .client import AiopsSDK
from .llm import LLMProvider, GeminiProvider
from .retrieval import IncidentSearch
from .remediation import Remediator
from .servicenow import ServiceNowClient

__version__ = "0.1.0"

__all__ = [
    "AiopsSDK",
    "LLMProvider",
    "GeminiProvider",
    "IncidentSearch",
    "Remediator",
    "ServiceNowClient",
]
