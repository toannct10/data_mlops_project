"""Explicit rollout guards. Never silently change analytical backends."""

import os
from dataclasses import dataclass


class AnalyticsNotAccepted(RuntimeError):
    pass


@dataclass(frozen=True)
class OnlineConfig:
    mode: str = "local"
    analytics_backend: str = "postgres"

    @classmethod
    def from_env(cls):
        result = cls(
            os.getenv("FOODY_MODE", "local"), os.getenv("ANALYTICS_BACKEND", "postgres")
        )
        if result.mode not in {"local", "cloud"}:
            raise ValueError("FOODY_MODE must be local or cloud")
        if result.analytics_backend not in {"postgres", "snowflake"}:
            raise ValueError("ANALYTICS_BACKEND must be postgres or snowflake")
        if result.mode == "cloud" and result.analytics_backend != "snowflake":
            raise AnalyticsNotAccepted(
                "Cloud mode requires ANALYTICS_BACKEND=snowflake; no PostgreSQL fallback."
            )
        return result


def require_local_analytics():
    config = OnlineConfig.from_env()
    if config.analytics_backend == "snowflake":
        from src.security.audit import security_event

        security_event("analytics_backend", "snowflake_not_accepted", status="blocked")
        raise AnalyticsNotAccepted(
            "Snowflake analytics are not accepted yet. Complete ingestion, dbt, reconciliation, "
            "dashboard and Snowflake SQL safety tests. No PostgreSQL fallback was used."
        )
    return config
