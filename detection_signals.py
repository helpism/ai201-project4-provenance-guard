"""Provenance Guard — Milestone 4
================================
Multi-Signal Ensemble Pipeline (analysis functions ONLY).

This module is intentionally standalone and framework-free so the math and
heuristics can be unit-tested in isolation before being wired into the Flask
app (`app.py`). It deliberately does NOT touch:
  * API endpoint routing
  * string label / attribution generation
  * the appeals workflow

It contains exactly three pieces of logic plus a self-test harness:
  * Signal 2 — Stylometric Heuristics  (`stylometric_score`)
  * Signal 3 — Word Frequency Entropy  (`entropy_score`)
  * Ensemble  — Weighted scoring engine (`calculate_combined_confidence`)

No heavy dependencies (no NLTK / spaCy). Standard library only: `re`,
`math`, `collections`.

Scoring convention (shared by every signal in this file):
    0.0  ->  strongly human  (variable, unpredictable, diverse)
    1.0  ->  strongly AI     (uniform, predictable, generic)
"""

import math
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Tunable calibration constants
# ---------------------------------------------------------------------------
# These reference values map raw linguistic measurements onto the 0.0–1.0
# scale. They are heuristic anchors, not trained parameters — adjust them as
# you gather real-world samples during calibration.

# Sentence-length variation. Coefficient of variation (std / mean) of words
# per sentence. Human prose swings between short and long sentences (high CV);
# AI prose is metronomic (low CV). At/above CV_HUMAN we call it fully human.
CV_HUMAN = 0.60

# Type-Token Ratio anchors. High TTR = rich, varied vocabulary (human).
# Low TTR = repetitive, recycled vocabulary (AI).
TTR_HUMAN = 0.72
TTR_AI = 0.38

# Normalized Shannon entropy anchor. Human text spreads probability mass
# across many tokens (entropy near its max). AI text concentrates mass on a
# smaller set of predictable tokens (lower normalized entropy).
ENTROPY_HUMAN = 0.92
ENTROPY_AI = 0.70

# A compact set of high-frequency English function words. A text dominated by
# these "top-tier predictable dictionary terms" reads as more generic/AI-like.
# (A deliberately small list — this is a heuristic, not a linguistics library.)
COMMON_WORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "as", "by", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "there", "their",
    "they", "them", "we", "our", "us", "he", "she", "his", "her", "i", "you",
    "your", "which", "who", "whom", "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "should", "may", "might", "must", "not",
    "from", "into", "about", "between", "while", "than", "such", "also",
    "however", "furthermore", "moreover", "therefore", "thus", "additionally",
}

# Ensemble weights (from planning.md). Chosen to lean on the LLM signal while
# letting the two cheap heuristics pull the result toward human to minimize
# false positives.
WEIGHT_LLM = 0.50
WEIGHT_STYLOMETRIC = 0.25
WEIGHT_ENTROPY = 0.25


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _clamp01(value: float) -> float:
    """Clamp any float into the inclusive [0.0, 1.0] range."""
    return max(0.0, min(1.0, value))


def _split_sentences(text: str) -> list:
    """Split text into sentences on ., !, ? (and newlines).

    Deliberately naive — good enough for a length-variance heuristic without
    dragging in a full NLP tokenizer. Empty fragments are dropped.
    """
    parts = re.split(r"[.!?\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _tokenize(text: str) -> list:
    """Lowercase word tokenizer. Returns alphanumeric word tokens only.

    Apostrophes are kept inside words (so "don't" / "work-life" collapse
    sensibly) but surrounding punctuation is stripped.
    """
    return re.findall(r"[a-z0-9']+", text.lower())


# ---------------------------------------------------------------------------
# Signal 2 — Stylometric Heuristics
# ---------------------------------------------------------------------------
def stylometric_score(text: str) -> float:
    """Score a text on structural uniformity (sentence variance + TTR).

    Two sub-metrics, each mapped to a 0.0 (human) .. 1.0 (AI) sub-score and
    then averaged:

      1. Sentence-length uniformity — the coefficient of variation of words
         per sentence. Low variation -> uniform -> AI-like.
      2. Vocabulary uniformity — the Type-Token Ratio (unique / total words).
         Low TTR -> repetitive vocabulary -> AI-like.

    Returns a float in [0.0, 1.0].
    """
    tokens = _tokenize(text)
    if not tokens:
        # No usable content — stay neutral rather than guess.
        return 0.5

    # --- Sub-metric 1: sentence-length variance ---------------------------
    sentences = _split_sentences(text)
    lengths = [len(_tokenize(s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]

    if len(lengths) < 2:
        # Can't measure variation with fewer than two sentences. Treat a
        # single block as neutral on this axis so it neither helps nor hurts.
        sentence_uniformity = 0.5
    else:
        mean_len = sum(lengths) / len(lengths)
        variance = sum((n - mean_len) ** 2 for n in lengths) / len(lengths)
        std_dev = math.sqrt(variance)
        cv = std_dev / mean_len if mean_len else 0.0
        # High CV (>= CV_HUMAN) -> human -> 0.0 ; zero CV -> AI -> 1.0
        sentence_uniformity = _clamp01(1.0 - (cv / CV_HUMAN))

    # --- Sub-metric 2: Type-Token Ratio -----------------------------------
    ttr = len(set(tokens)) / len(tokens)
    # High TTR (>= TTR_HUMAN) -> human -> 0.0 ; low TTR (<= TTR_AI) -> AI -> 1.0
    ttr_uniformity = _clamp01((TTR_HUMAN - ttr) / (TTR_HUMAN - TTR_AI))

    # --- Combine ----------------------------------------------------------
    return _clamp01(0.5 * sentence_uniformity + 0.5 * ttr_uniformity)


# ---------------------------------------------------------------------------
# Signal 3 — Word Frequency Entropy
# ---------------------------------------------------------------------------
def entropy_score(text: str) -> float:
    """Score a text on the predictability of its word distribution.

    Two sub-metrics, each mapped to a 0.0 (human) .. 1.0 (AI) sub-score and
    then averaged:

      1. Normalized Shannon entropy of the token frequency distribution.
         High entropy = probability mass spread widely = unpredictable/human.
         Low entropy  = mass concentrated on few tokens = predictable/AI.
      2. Common-word saturation — the fraction of tokens drawn from the
         high-frequency function-word set. AI text over-indexes on these
         "top-tier predictable" terms.

    Returns a float in [0.0, 1.0].
    """
    tokens = _tokenize(text)
    if not tokens:
        return 0.5

    counts = Counter(tokens)
    total = len(tokens)

    # --- Sub-metric 1: normalized Shannon entropy -------------------------
    entropy = -sum(
        (c / total) * math.log2(c / total) for c in counts.values()
    )
    # Normalize against the maximum possible entropy for this vocabulary size
    # (log2 of the number of distinct tokens) so the value is comparable
    # across texts of different lengths -> [0.0, 1.0], higher = more diverse.
    distinct = len(counts)
    max_entropy = math.log2(distinct) if distinct > 1 else 1.0
    norm_entropy = entropy / max_entropy if max_entropy else 1.0

    # High norm_entropy (>= ENTROPY_HUMAN) -> human -> 0.0
    # Low  norm_entropy (<= ENTROPY_AI)    -> AI    -> 1.0
    entropy_component = _clamp01(
        (ENTROPY_HUMAN - norm_entropy) / (ENTROPY_HUMAN - ENTROPY_AI)
    )

    # --- Sub-metric 2: common-word saturation -----------------------------
    common_hits = sum(counts[w] for w in counts if w in COMMON_WORDS)
    common_ratio = common_hits / total
    # Empirically, ~35% function words reads as generic; ~15% reads as
    # distinctive/human. Map that band onto [0.0, 1.0].
    common_component = _clamp01((common_ratio - 0.15) / (0.35 - 0.15))

    # --- Combine ----------------------------------------------------------
    return _clamp01(0.5 * entropy_component + 0.5 * common_component)


# ---------------------------------------------------------------------------
# Ensemble — Weighted Scoring Engine
# ---------------------------------------------------------------------------
def calculate_combined_confidence(llm_score: float,
                                  stylometric_score: float,
                                  entropy_score: float) -> float:
    """Combine the three signals into a single AI-likelihood confidence.

    Uses the fixed weighting from planning.md:

        Confidence = 0.50 * LLM
                   + 0.25 * Stylometric
                   + 0.25 * Entropy

    Each input is clamped to [0.0, 1.0] first so a misbehaving upstream
    signal can never push the confidence out of range. Returns a float in
    [0.0, 1.0].
    """
    llm = _clamp01(llm_score)
    style = _clamp01(stylometric_score)
    entropy = _clamp01(entropy_score)

    confidence = (
        WEIGHT_LLM * llm
        + WEIGHT_STYLOMETRIC * style
        + WEIGHT_ENTROPY * entropy
    )
    return _clamp01(confidence)


# ---------------------------------------------------------------------------
# Standalone self-test harness
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # (label, text, mocked LLM score). The LLM score stands in for Signal 1
    # (Groq) so this module stays fully offline and dependency-free.
    SAMPLES = [
        (
            "Clearly AI",
            "Artificial intelligence represents a transformative paradigm "
            "shift in modern society. It is important to note that while the "
            "benefits of AI are numerous, it is equally essential to consider "
            "the ethical implications. Furthermore, stakeholders across "
            "various sectors must collaborate to ensure responsible "
            "deployment.",
            0.90,
        ),
        (
            "Clearly Human",
            "ok so i finally tried that new ramen place downtown and "
            "honestly? underwhelming. the broth was fine but they put WAY too "
            "much sodium in it and i was thirsty for like three hours after. "
            "my friend got the spicy version and said it was better. probably "
            "won't go back unless someone drags me there",
            0.10,
        ),
        (
            "Formal Human",
            "The relationship between monetary policy and asset price "
            "inflation has been extensively studied in the literature. "
            "Central banks face a fundamental tension between their mandate "
            "for price stability and the unintended consequences of prolonged "
            "low interest rates on equity and real estate valuations.",
            0.35,
        ),
        (
            "Edited AI",
            "I've been thinking a lot about remote work lately. There are "
            "genuine tradeoffs — flexibility and no commute on one side, "
            "isolation and blurred work-life boundaries on the other. Studies "
            "show productivity varies widely by individual and role type.",
            0.60,
        ),
    ]

    header = (
        f"{'Sample':<15} {'LLM':>6} {'Stylo':>7} {'Entropy':>8} "
        f"{'Confidence':>11}"
    )
    print("\nProvenance Guard — Milestone 4 signal self-test")
    print("(scale: 0.0 = human  ->  1.0 = AI)\n")
    print(header)
    print("-" * len(header))

    for label, text, mock_llm in SAMPLES:
        style = stylometric_score(text)
        entropy = entropy_score(text)
        confidence = calculate_combined_confidence(mock_llm, style, entropy)
        print(
            f"{label:<15} {mock_llm:>6.2f} {style:>7.3f} {entropy:>8.3f} "
            f"{confidence:>11.3f}"
        )

    print(
        "\nExpected trend: 'Clearly AI' highest confidence, 'Clearly Human' "
        "lowest,\nwith the two mixed samples landing in between.\n"
    )
