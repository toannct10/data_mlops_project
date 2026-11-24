"""Ad-hoc PhoBERT ABSA Review analysis; historical baselines stay in research."""

import streamlit as st

from src.foody.review_models.registry import analyze_models
from src.ordering.sentiment import overall_sentiment
from src.ui.components import kpi_card
from src.ui.progress import CLASSIFIER_STAGES, AIProgress


def _render_model_card(title, outcome):
    with st.container(border=True):
        st.markdown(f"#### {title}")
        if outcome["status"] != "available":
            st.warning("Model unavailable")
            with st.expander("Technical Details"):
                st.write("Inference could not complete.")
                st.write("Error type:", outcome.get("error_type", "Unknown"))
            return
        result = outcome["result"]
        if result.get('relevance_label') == 'NOT_RELEVANT':
            st.markdown('<div style="background:#303640;border:1px solid #59616d;border-radius:12px;padding:18px"><strong>NOT RELEVANT</strong><p>This review is not related to restaurant or dining experience.</p></div>', unsafe_allow_html=True)
            st.caption('PhoBERT ABSA skipped because the review was classified as not relevant.')
            st.caption('Weakly-supervised relevance gate; not a human-validated spam classifier.')
            if result.get('proxy_decision') in {'UNCERTAIN','SHORT_DOMAIN_GUARDRAIL'}:
                st.caption('Proxy score is uncertain for this input; routed using documented restaurant-domain rules.')
            return
        sentiment, reason = overall_sentiment(result["aspects"])
        kpi_card("Overall sentiment", sentiment.title(), kind="primary")
        if reason:
            st.caption(reason)
        if result["sentiment_score"] is not None:
            st.caption(f"Model score: {result['sentiment_score']:.1%} (uncalibrated)")
        st.caption(f"Inference latency: {result['latency_ms']:.1f} ms")
        if result["aspects"]:
            for item in result["aspects"]:
                st.write(f"{item['label']} → {item['sentiment'].title()}"
                         f" · Model score {item['score']:.1%}")
        else:
            st.info("No aspect was detected by this model.")
        with st.expander("Technical Details"):
            st.json({"model_name": result["model_name"],
                     "supported_tasks": result["supported_tasks"],
                     "latency_ms": result["latency_ms"],
                     **result["metadata"], "raw_output": result["raw_output"]})


def render_review_analysis():
    with st.container(border=True):
        st.subheader("Analyze Customer Review")
        st.caption("PhoBERT ABSA predicts 12 aspect sentiments. Overall sentiment follows "
                   "the deterministic aspect rule. Text is not added to the Review corpus.")
        review = st.text_area("Review text", key="adhoc_review_text", height=150,
                              placeholder="Đồ ăn ngon nhưng phục vụ hơi chậm và giá cao.")
        submitted = st.button("Analyze Review", type="primary", key="analyze_review",
                              disabled=not review.strip())
    if submitted:
        st.session_state.pop("_adhoc_review_result", None)
        progress = AIProgress("Analyzing review")
        try:
            progress.stage(CLASSIFIER_STAGES[0], 0.1)
            progress.stage(CLASSIFIER_STAGES[1], 0.5)
            results = analyze_models(review, "PhoBERT ABSA")
            progress.stage(CLASSIFIER_STAGES[2], 0.95)
            st.session_state["_adhoc_review_result"] = {
                "scope": review, "results": results}
            progress.finish()
        except Exception:
            progress.finish(success=False)
            st.error("Review analysis is temporarily unavailable. Please try again later.")
            return
    saved = st.session_state.get("_adhoc_review_result")
    if not saved or saved["scope"] != review:
        return
    _render_model_card("PhoBERT ABSA Model", saved["results"]["phobert_absa"])
