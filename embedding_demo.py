from sentence_transformers import SentenceTransformer
import numpy as np

# Same model your real pipeline uses - this script exists purely to make the
# "similar meaning -> close vectors" idea visible with real numbers, using
# short incident-style sentences instead of your full corpus.

SENTENCES = [
    "Robot lost connection to Orchestrator mid-transaction, queue items stuck In Progress.",   # A
    "Unattended bot dropped offline overnight, queue items left In Progress.",                 # B - similar to A
    "Robot process crashed and orphaned queue items remained In Progress for hours.",           # C - similar to A/B
    "Excel workbook activity failed because the file was locked by another process.",           # D - unrelated
    "The office printer keeps jamming on double-sided print jobs.",                              # E - totally unrelated
]

LABELS = ["A: lost connection", "B: dropped offline", "C: crashed/orphaned", "D: Excel locked", "E: printer jam"]


def l2_distance(v1, v2):
    return float(np.sqrt(np.sum((v1 - v2) ** 2)))


def main():
    print("Loading all-MiniLM-L6-v2 (same model as your real pipeline)...\n")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    vectors = model.encode(SENTENCES, convert_to_numpy=True)

    print(f"Each sentence became a vector with {vectors.shape[1]} numbers.")
    print("Here are just the first 5 numbers of each, so you can see they're real, different numbers:\n")
    for label, vec in zip(LABELS, vectors):
        preview = ", ".join(f"{x:.3f}" for x in vec[:5])
        print(f"  {label:22s} [{preview}, ...]")

    print("\nNow the actual L2 distance between every pair (smaller = more similar):\n")
    header = "                        " + "".join(f"{l:>22s}" for l in LABELS)
    print(header)
    for i, label_i in enumerate(LABELS):
        row = f"  {label_i:22s}"
        for j in range(len(LABELS)):
            d = l2_distance(vectors[i], vectors[j])
            row += f"{d:22.3f}"
        print(row)

    print(
        "\nLook at A/B/C (all about a robot losing connection) vs D and E "
        "(unrelated) - A/B/C should be noticeably closer to each other than "
        "to D or E, even though they don't share many exact words. That's "
        "the entire mechanism your retrieval step relies on."
    )


if __name__ == "__main__":
    main()
