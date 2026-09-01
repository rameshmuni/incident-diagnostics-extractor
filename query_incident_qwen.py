import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from query_incident import (
    ask_human_verdict,
    build_prompt,
    embed_query,
    escalate_to_developer,
    load_index,
    log_feedback,
    parse_slm_pick,
    search,
)

# Same retrieval + human-verdict + logging + escalation logic as query_incident.py.
# The only thing that's different is the reasoning step: instead of calling Ollama's
# local HTTP server, this loads Qwen3 directly into the process with transformers
# and generates locally - a genuinely different mechanism, not just a different URL.

# If your Artifactory download used a different identifier/local path than the
# public Hugging Face repo id, replace this one line with that exact value.
QWEN_MODEL_PATH = "Qwen/Qwen3-8B"

_tokenizer = None
_model = None


def get_model():
    # lazy-loaded and cached, same pattern as the embedding model cache in
    # query_incident.py - loading an 8B model takes real time and memory, so this
    # only happens once per run, not once per query
    global _tokenizer, _model
    if _model is None:
        print(f"Loading {QWEN_MODEL_PATH} (can take a couple of minutes on first run)...")
        _tokenizer = AutoTokenizer.from_pretrained(QWEN_MODEL_PATH)
        _model = AutoModelForCausalLM.from_pretrained(
            QWEN_MODEL_PATH,
            torch_dtype=torch.bfloat16,  # halves memory vs full precision - ~16GB instead of ~32GB
            device_map="auto",
        )
    return _tokenizer, _model


def call_qwen(prompt, max_new_tokens=400):
    tokenizer, model = get_model()

    # Qwen3 is instruction-tuned and expects a chat-formatted prompt, not raw text -
    # apply_chat_template is what makes it actually answer the question instead of
    # just continuing the sentence the way a base model like GPT-2 would
    messages = [{"role": "user", "content": prompt}]
    input_ids = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
    ).to(model.device)

    with torch.no_grad():
        output_ids = model.generate(
            input_ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,  # deterministic - same incident in, same answer out
        )

    new_tokens = output_ids[0][input_ids.shape[-1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def main(new_incident_text=None):
    # entry point: identical flow to query_incident.py's main(), with call_qwen()
    # standing in for call_slm()
    if new_incident_text is None:
        new_incident_text = input("Describe the new incident: ")

    index, metadata = load_index()
    query_vector = embed_query(new_incident_text)
    matches = search(index, metadata, query_vector)

    print("\nClosest past incidents:")
    for m in matches:
        print(f"  {m['incident_id']}  (distance {m['distance']:.3f})  {m['short_description']}")

    prompt = build_prompt(new_incident_text, matches)
    print("\nAsking Qwen3 (running locally)...\n")
    suggestion = call_qwen(prompt)
    print(suggestion)

    slm_pick = parse_slm_pick(suggestion, matches)
    verdict = ask_human_verdict(matches, slm_pick)
    log_feedback(new_incident_text, matches, slm_pick, verdict)

    if verdict["verdict"] == "confirmed":
        print(f"\nLogged as confirmed. Final answer stands as above ({slm_pick}).")
        return suggestion
    elif verdict["verdict"] == "corrected":
        print(f"\nLogged your correction. Using {verdict['chosen_id']}'s actual record instead:\n")
        print(verdict["chosen_text"])
        return verdict["chosen_text"]
    else:
        escalate_to_developer(new_incident_text, matches)
        return None


if __name__ == "__main__":
    arg_text = " ".join(sys.argv[1:]) or None
    main(arg_text)