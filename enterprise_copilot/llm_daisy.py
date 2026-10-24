"""
Talks to Daisy LLM's chat-completions endpoint (OpenAI-compatible, Bearer auth).

This is the ONE file that knows Daisy's request/response shape. If you ever
need to point this project at a different LLM, copy this file to e.g.
llm_other.py, change the two lines marked below to match that LLM's actual
API, and change the one `import llm_daisy as llm` line in query_incident.py
to `import llm_other as llm`. Nothing else in the project needs to change -
every caller only ever uses chat_completion(prompt) -> str.
"""
import requests

import config


def chat_completion(prompt, system_prompt=None, temperature=0.2, max_tokens=800):
    """Send one prompt, get back the model's text reply."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    url = f"{config.DAISY_BASE_URL.rstrip('/')}/chat/completions"  # <- change if Daisy's path differs
    headers = {
        "Authorization": f"Bearer {config.DAISY_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.DAISY_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    resp = requests.post(url, headers=headers, json=body, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Daisy LLM request failed: {resp.status_code}\n{resp.text[:500]}")

    data = resp.json()
    try:
        # Standard OpenAI-compatible shape: {"choices": [{"message": {"content": "..."}}]}
        return data["choices"][0]["message"]["content"]  # <- change if Daisy's response shape differs
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Daisy LLM response shape: {data}") from exc


if __name__ == "__main__":
    # Quick manual check: `python llm_daisy.py` should print a short reply.
    print(chat_completion("Say hello in exactly five words."))
