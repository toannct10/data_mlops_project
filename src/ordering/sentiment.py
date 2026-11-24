"""One deterministic overall sentiment rule for PhoBERT aspect output."""


def overall_sentiment(aspects):
    labels = [str(item["sentiment"]).lower() for item in aspects
              if str(item["sentiment"]).lower() != "not_mentioned"]
    if not labels:
        return "NEUTRAL", "No aspect confidently detected"
    if any(label not in {"positive", "negative", "neutral"} for label in labels):
        raise ValueError("Unexpected PhoBERT aspect sentiment")
    if all(label == "positive" for label in labels):
        return "POSITIVE", None
    if all(label == "negative" for label in labels):
        return "NEGATIVE", None
    return "NEUTRAL", None
