"""
The single front door. A consumer creates one AiopsSDK, once, and calls it
for everything from then on - retrieval, remediation, ServiceNow, and
whichever LLM they chose at construction time. This is the object app.py's
routes would call instead of importing query_incident / query_incident_gemini
/ week3.auto_remediation individually - see aiops_sdk/README.md for exactly
what would change in app.py to adopt this, none of which has been done yet.
"""

from .llm import LLMProvider, GeminiProvider
from .retrieval import IncidentSearch
from .remediation import Remediator
from .servicenow import ServiceNowClient


class AiopsSDK:
    """
    sdk = AiopsSDK()                              # defaults to GeminiProvider
    sdk = AiopsSDK(llm_provider=SomeOtherProvider())   # swap the model, nothing else changes

    sdk.suggest(text)                                   # Week 2 flow: retrieve + suggest, human still decides
    sdk.propose_remedy(text)                            # step 1 of the required flow: match only, no fix runs
    sdk.run_confirmed_remedy(text, issue_id, approver)   # step 2: only after a human approves step 1's result

    sdk.auto_remediate(text)   # the original fully-automatic flow (no human, ever) - kept
                                # for comparison with what's deployed today; per current
                                # direction, propose_remedy()/run_confirmed_remedy() is the
                                # flow to actually use, since a human must approve every fix.
    """

    def __init__(self, llm_provider: LLMProvider = None):
        self.llm_provider = llm_provider or GeminiProvider()
        self.search = IncidentSearch()
        self.remediate = Remediator()
        self.servicenow = ServiceNowClient()

    def suggest(self, incident_text: str, k: int = 5) -> dict:
        """Week 2's retrieve-then-suggest flow as one call. Returns the
        retrieved matches plus the LLM's suggestion text - the caller still
        owns the human-verdict step (confirm / correct / none apply); this
        method never writes anything to ServiceNow itself."""
        matches = self.search.find_similar(incident_text, k=k)
        prompt = self.search.build_prompt(incident_text, matches)
        suggestion = self.llm_provider.generate(prompt)
        return {"matches": matches, "suggestion": suggestion}

    def propose_remedy(self, incident_text: str) -> dict:
        """Step 1 of the required human-in-the-loop remediation flow -
        matches only, never runs a fix, never touches ServiceNow. Show this
        result to a person before ever calling run_confirmed_remedy()."""
        return self.remediate.propose(incident_text)

    def run_confirmed_remedy(self, incident_text: str, issue_id: str, approved_by: str = None) -> dict:
        """Step 2 - runs the real fix and resolves the incident. Only call
        this after a human has seen propose_remedy()'s result and approved
        issue_id - this method itself does not check confidence or ask
        anyone; the caller invoking it at all is the approval."""
        return self.remediate.run_confirmed_fix(incident_text, issue_id, approved_by=approved_by)

    def auto_remediate(self, incident_text: str) -> dict:
        """The original fully-automatic flow: match, run the real fix, and
        create + resolve the ServiceNow incident - or escalate - with no
        human involved anywhere. Unmodified, matches what's deployed today.
        Kept for reference; propose_remedy()/run_confirmed_remedy() above is
        the flow to use now that every fix requires human approval."""
        return self.remediate.attempt(incident_text)

    def __repr__(self):
        return f"AiopsSDK(llm_provider={self.llm_provider!r})"
