"""Optional backend for Docker/API users with a shared review retriever."""

import json
import os
import subprocess
from pathlib import Path
from time import perf_counter
from src.monitoring.ai_timing import capture

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from src.monitoring.metrics import API_DURATION, API_REQUESTS, observe_rag, observe_rag_error
from src.security.audit import security_event

api = FastAPI(title="Foody grounded review API")

class SalesAIRequest(BaseModel):
    restaurant_id: str = Field(min_length=1,max_length=80)
    question: str = Field(min_length=1,max_length=2000)

@api.post('/sales-ai')
def sales_ai(body: SalesAIRequest):
    """Read-only HTTP access to the exact Sales service used by Streamlit."""
    from src.sales.service import answer_question
    from src.warehouse.loader import connection
    try:
        with connection() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            with capture() as timings:
                result=answer_question(conn,body.restaurant_id,body.question)
            result.setdefault('telemetry',{})['stage_timings_seconds']=timings
            return result
    except ValueError as error:
        raise HTTPException(status_code=422,detail=str(error)) from None
    except Exception as error:
        security_event('sales_api',type(error).__name__)
        raise HTTPException(status_code=503,detail='Chưa thể truy vấn dữ liệu bán hàng lúc này.') from None


class OrderLine(BaseModel):
    variant_id: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=99)


class NewOrder(BaseModel):
    restaurant_id: str
    lines: list[OrderLine] = Field(min_length=1)


class NewReview(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=1)


@api.get("/ordering/restaurants")
def ordering_restaurants():
    from src.ordering.menu import list_live_restaurants
    from src.warehouse.loader import connection

    with connection() as conn:
        return list_live_restaurants(conn)


@api.get("/ordering/restaurants/{restaurant_id}/menu")
def ordering_menu(restaurant_id: str):
    from src.ordering.menu import list_menu
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            return list_menu(conn, restaurant_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@api.post("/ordering/orders", status_code=201)
def ordering_create(body: NewOrder):
    from src.monitoring.metrics import LIVE_ORDER_CREATED
    from src.ordering.service import create_order
    from src.ordering.snowflake_sync import sync_entity
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            result = create_order(conn, body.restaurant_id,
                                  [line.model_dump() for line in body.lines])
        with connection() as conn:
            result["sync"] = sync_entity(conn, "order", result["order_id"])
        LIVE_ORDER_CREATED.inc()
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@api.get("/ordering/orders")
def ordering_orders(restaurant_id: str | None = None):
    from src.ordering.service import list_orders
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            return list_orders(conn, restaurant_id=restaurant_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@api.get("/ordering/orders/{order_id}")
def ordering_order(order_id: str):
    from src.ordering.service import get_order
    from src.warehouse.loader import connection

    with connection() as conn:
        result = get_order(conn, order_id)
        if result is None or result["source"] != "live_web":
            raise HTTPException(status_code=404, detail="Live order not found")
        sync = conn.execute("""SELECT cloud_status FROM live_sync_state
            WHERE entity_type='order' AND entity_id=%s""", (order_id,)).fetchone()
        result["sync"] = {"local_db": "SAVED",
                          "snowflake": sync[0] if sync else "NOT_CONFIGURED"}
        return result


@api.post("/ordering/orders/{order_id}/advance")
def ordering_advance(order_id: str):
    return _ordering_status(order_id, cancel=False)


@api.post("/ordering/orders/{order_id}/cancel")
def ordering_cancel(order_id: str):
    return _ordering_status(order_id, cancel=True)


def _ordering_status(order_id, *, cancel):
    from src.monitoring.metrics import LIVE_CANCELLED, LIVE_DELIVERED, LIVE_STATUS_CHANGED
    from src.ordering.service import change_status
    from src.ordering.snowflake_sync import sync_entity
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            result = change_status(conn, order_id, cancel=cancel)
        with connection() as conn:
            result["sync"] = sync_entity(conn, "order", order_id)
        LIVE_STATUS_CHANGED.labels(status=result["status"]).inc()
        if result["status"] == "DELIVERED":
            LIVE_DELIVERED.inc()
        if result["status"] == "CANCELLED":
            LIVE_CANCELLED.inc()
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@api.get("/ordering/restaurants/{restaurant_id}/live-today")
def ordering_live_today(restaurant_id: str):
    from src.ordering.service import live_daily_facts
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            return live_daily_facts(conn, restaurant_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@api.post("/ordering/orders/{order_id}/review", status_code=201)
def ordering_review(order_id: str, body: NewReview):
    import logging

    from src.monitoring.metrics import LIVE_REVIEWS, RAG_INDEX_UPDATES
    from src.ordering.index import upsert_live_review
    from src.ordering.service import submit_review
    from src.ordering.snowflake_sync import sync_entity
    from src.warehouse.loader import connection

    try:
        with connection() as conn:
            result = submit_review(conn, order_id, body.rating, body.comment)
        LIVE_REVIEWS.inc()
        with connection() as conn:
            result["sync"] = sync_entity(conn, "review", result["review_id"])
        if result.get('relevance_label') == 'NOT_RELEVANT':
            result['index'] = {'status':'SKIPPED_NOT_RELEVANT'}
            return result
        try:
            with connection() as conn:
                result["index"] = upsert_live_review(conn, result["review_id"])
            RAG_INDEX_UPDATES.labels(status="success").inc()
        except Exception:
            logging.exception("Incremental live Review index update failed")
            RAG_INDEX_UPDATES.labels(status="failed").inc()
            result["index"] = {"status": "FAILED"}
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


class Question(BaseModel):
    question: str = Field(min_length=1)
    restaurant: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@api.middleware("http")
async def count_requests(request, call_next):
    started = perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    label = route.path if route is not None else "unmatched"
    API_REQUESTS.labels(route=label, status=str(response.status_code)).inc()
    API_DURATION.labels(route=label).observe(perf_counter() - started)
    return response


@api.get("/metrics")
def metrics():
    from src.monitoring.operations import refresh_prometheus

    refresh_prometheus()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@api.get("/version")
def version():
    import yaml

    platform = yaml.safe_load(Path("config/platform_versions.yaml").read_text(encoding="utf-8"))
    demo = yaml.safe_load(Path("config/demo.yaml").read_text(encoding="utf-8"))
    versions_path = Path("reports/foody_intelligence/versions.json")
    versions = json.loads(versions_path.read_text(encoding="utf-8")) if versions_path.exists() else {}
    embedding_path = Path("reports/rag/embedding_selection.json")
    embedding = json.loads(embedding_path.read_text(encoding="utf-8")) if embedding_path.exists() else {}
    qwen_path = Path(demo["qwen_provenance"])
    qwen = json.loads(qwen_path.read_text(encoding="utf-8")) if qwen_path.exists() else {}
    from src.foody.review_runtime import active_manifest, new_corpus_active

    review = active_manifest()
    sha = os.getenv("GIT_SHA")
    if not sha and Path(".git").exists():
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=False).stdout.strip() or None
    return {"project_version": platform["project_version"], "git_sha": sha,
            "warehouse_schema_version": platform["warehouse_schema_version"],
            "event_schema_version": platform["event_schema_version"],
            "rag_version": demo["rag_version"], "prompt_id": demo["prompt_version"],
            "sentiment_sha256": versions.get("sentiment_model_sha256"),
            "aspect_sha256": versions.get("aspect_model_sha256"),
            "dataset_sha256": (review.get("source_sha256") if new_corpus_active() else
                               versions.get("source_sha256")),
            "review_data_version": review.get("data_version", "legacy"),
            "review_count": review.get("canonical_row_count", review.get("row_count", review.get("document_count"))),
            "embedding_model": demo["embedding_model"],
            "embedding_revision": embedding.get("revision"),
            "embedding_sha256": embedding.get("model_hash"),
            "qwen_model": demo["qwen_model"], "qwen_revision": qwen.get("revision"),
            "qwen_sha256": qwen.get("model_sha256")}


@api.post("/ask-v3")
def ask_v3(body: Question):
    """Monitor the accepted V3 service without changing its retrieval/answer logic."""
    try:
        from src.foody.review_runtime import new_corpus_active, review_identity
        from src.rag.schemas import RetrievalRequest
        from src.rag.service import resolve_restaurant
        from src.rag_v3.cli import selected_v3_gateway
        from src.rag_v3.retriever import cached_retriever
        from src.rag_v3.service import answer_question_v3

        key = (review_identity(body.restaurant)[0] if new_corpus_active() else
               resolve_restaurant(body.restaurant)[0]) if body.restaurant else None
        with selected_v3_gateway() as gateway:
            answer, hits, telemetry, _, _ = answer_question_v3(
                cached_retriever(), gateway,
                RetrievalRequest(query=body.question, top_k=body.top_k,
                                 **({"restaurant_id": key} if new_corpus_active() else
                                    {"restaurant_key": key})),
            )
        observe_rag(telemetry)
        return {"response": answer.model_dump(),
                "retrieved": [hit.model_dump() for hit in hits], "telemetry": telemetry}
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        observe_rag_error()
        security_event("rag_api", type(error).__name__)
        raise HTTPException(status_code=503, detail="Review service unavailable") from None


@api.get("/health")
def health():
    from src.foody.review_runtime import VECTOR, new_corpus_active

    vector = VECTOR if new_corpus_active() else Path("data/foody/vector_store")
    return {"status": "ready", "vector_index_available": Path(
        vector / "index_manifest.json").exists(),
        "qwen_weights_available": Path("models/qwen_v3/qwen2.5-3b-instruct-q4_k_m.gguf").exists()}


@api.post("/ask")
def ask(body: Question):
    try:
        from src.foody.review_runtime import new_corpus_active, review_identity
        from src.llm.gateway import LLMGateway
        from src.rag.cli import selected_retriever
        from src.rag.schemas import RetrievalRequest
        from src.rag.service import answer_question, resolve_restaurant

        key = (review_identity(body.restaurant)[0] if new_corpus_active() else
               resolve_restaurant(body.restaurant)[0]) if body.restaurant else None
        with LLMGateway() as gateway:
            answer, hits, telemetry = answer_question(
                selected_retriever(), gateway,
                RetrievalRequest(query=body.question, top_k=body.top_k,
                                 **({"restaurant_id": key} if new_corpus_active() else
                                    {"restaurant_key": key})),
            )
        return {"response": answer.model_dump(), "retrieved": [hit.model_dump() for hit in hits],
                "telemetry": telemetry}
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        security_event("rag_api", type(error).__name__)
        raise HTTPException(status_code=503, detail="Review service unavailable") from None


@api.get("/restaurants/{restaurant}/health-report")
def report(restaurant: str):
    try:
        from src.llm.gateway import LLMGateway
        from src.rag.cli import selected_retriever
        from src.rag.service import health_report

        with LLMGateway() as gateway:
            result, _, telemetry = health_report(selected_retriever(), gateway, restaurant)
        return {"report": result.model_dump(), "telemetry": telemetry}
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        security_event("rag_api", type(error).__name__)
        raise HTTPException(status_code=503, detail="Review service unavailable") from None

class CrossChatRequest(BaseModel):
    question: str = Field(min_length=1,max_length=2000)
    restaurant_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    conversation_state: dict = Field(default_factory=dict)

@api.post('/cross-analysis')
def cross_analysis_chat(body: CrossChatRequest):
    from src.sales.cross_chat import analyze_chat
    from src.warehouse.loader import connection
    try:
        with connection() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            with capture() as timings:
                result=analyze_chat(conn,body.question,selected_id=body.restaurant_id,start=body.start_date,end=body.end_date,state=body.conversation_state)
            result.setdefault('telemetry',{})['stage_timings_seconds']=timings
            return result
    except ValueError as error:
        raise HTTPException(status_code=422,detail=str(error)) from None
    except Exception:
        import logging
        logging.exception('Canonical Cross Analysis failed')
        raise HTTPException(status_code=503,detail='Cross Analysis is temporarily unavailable; canonical data is preserved.') from None
