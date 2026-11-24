"""One restaurant context for current SQL sales and PhoBERT review intelligence."""
import os
from src.online_config import AnalyticsNotAccepted, require_local_analytics
from datetime import date
from html import escape
import pandas as pd
import streamlit as st
from src.warehouse.loader import connection
from src.ordering.menu import LIVE_IDS
from src.sales.queries import vietnam_today


def frame(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    return pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])


def render_business(review_ai):
    st.markdown("""<style>h1{font-size:30px!important}h2{font-size:23px!important}
    [data-testid='stMetricValue']{font-variant-numeric:tabular-nums}
    [data-testid='stMetric']:first-child{border-color:#526c91}
    </style>""", unsafe_allow_html=True)
    st.title("Business Intelligence")
    st.caption("Sales performance and customer intelligence, in one place.")
    try:
        require_local_analytics()
        with connection() as conn:
            restaurants = frame(conn, """SELECT restaurant_id,COALESCE(MAX(restaurant_name), 'Unnamed restaurant') restaurant_name
                FROM (SELECT restaurant_id,restaurant_name FROM dim_sales_restaurant
                UNION ALL SELECT restaurant_id,restaurant_name FROM dim_review_restaurant_v2) r
                GROUP BY restaurant_id ORDER BY (restaurant_id IN ('145','1803','2642')) DESC,restaurant_name,restaurant_id""")
            names = dict(zip(restaurants.restaurant_id.astype(str), restaurants.restaurant_name))
            left, middle, right = st.columns((3, 2, 1))
            with left:
                selected = st.selectbox("Restaurant", list(names),
                    format_func=lambda key: ("⭐ " if key in LIVE_IDS else "") + names[key],
                    key="business_context_restaurant")
                if selected in LIVE_IDS:st.caption("⭐ Live ordering enabled")
            with middle:
                period = st.date_input("Period", (date(2025,1,1), vietnam_today()), key="business_period")
            with right:
                scope = st.selectbox("Data scope", ["All data", "Historical", "Live orders"])
                st.button("Refresh", use_container_width=True)
            if len(period) != 2:
                st.info("Choose a start and end date."); return
            start, end = sorted(period)
            source = {"All data": None, "Historical": "historical", "Live orders": "live_web"}[scope]
            args = (selected, start, end, source, source)
            condition = "restaurant_id=%s AND d.calendar_date BETWEEN %s AND %s AND (%s::text IS NULL OR source=%s)"
            orders = frame(conn, """SELECT d.calendar_date,UPPER(order_status) status,
                COUNT(*) orders,COALESCE(SUM(total_amount),0)::float amount
                FROM fact_order JOIN dim_date d USING(date_key) WHERE """ + condition +
                " GROUP BY d.calendar_date,UPPER(order_status) ORDER BY d.calendar_date", args)
            reviews = frame(conn, """SELECT review_id,review_text,
                rating * CASE WHEN source='live_web' THEN 2 ELSE 1 END AS rating,overall_sentiment,source,
                absa_model_version,d.calendar_date FROM fact_review_v2 JOIN dim_date d USING(date_key)
                WHERE """ + condition + " ORDER BY d.calendar_date DESC", args)
            aspects = frame(conn, """SELECT a.aspect,a.sentiment,COUNT(*) mentions
                FROM fact_review_aspect a JOIN fact_review_v2 r ON r.review_id=a.review_id AND r.absa_model_version=a.model_version
                JOIN dim_date d ON d.date_key=r.date_key
                WHERE r.restaurant_id=%s AND d.calendar_date BETWEEN %s AND %s
                AND (%s::text IS NULL OR r.source=%s) AND r.absa_model_version IS NOT NULL
                AND r.relevance_label IS DISTINCT FROM 'NOT_RELEVANT'
                GROUP BY a.aspect,a.sentiment""", args)
        st.subheader(names[selected])
        if selected in LIVE_IDS:
            st.link_button("Open Live Ordering ↗", os.getenv("FOODY_ORDERING_PUBLIC_URL", "http://localhost:8502").rstrip("/") + "/?restaurant=" + selected)
        st.header("Sales Performance")
        delivered = orders.loc[orders.status.eq("DELIVERED")]
        revenue, count = delivered.amount.sum(), int(delivered.orders.sum())
        cancelled = int(orders.loc[orders.status.eq("CANCELLED"), "orders"].sum())
        for col, label, value in zip(st.columns((2,1,1,1)),
            ["Delivered revenue", "Delivered orders", "Average order value", "Cancelled orders"],
            [f"{revenue:,.0f} ₫", f"{count:,}", f"{revenue/count if count else 0:,.0f} ₫", str(cancelled)]):
            col.metric(label, value)
        if not orders.empty:
            left, right = st.columns((2,1))
            with left:
                st.subheader("Revenue trend")
                st.line_chart(delivered.groupby("calendar_date").amount.sum(), color="#e3ad69")
                st.subheader("Order trend")
                st.bar_chart(orders.groupby("calendar_date").orders.sum())
            with right:
                st.subheader("Orders by status")
                st.bar_chart(orders.groupby("status").orders.sum(), color="#88b8ac")
        else: st.info("No sales in this period. Try another period or data scope.")
        st.divider(); st.header("Customer Reviews")
        enriched = reviews.loc[reviews.absa_model_version.notna() & reviews.overall_sentiment.ne("NOT_RELEVANT")]
        overall = enriched.overall_sentiment.mode()
        c1,c2,c3 = st.columns(3)
        c1.metric("Reviews", len(reviews))
        c2.metric("Average rating /10", f"{pd.to_numeric(reviews.rating).mean():.1f}" if reviews.rating.notna().any() else "—")
        c3.metric("Overall sentiment", overall.iloc[0].title() if len(overall) else "Not available")
        st.subheader("PhoBERT · 12-aspect intelligence")
        st.caption("Aspect and sentiment results use PhoBERT enrichment. Unenriched reviews are excluded from AI summaries.")
        if not aspects.empty:
            aspects['aspect'] = aspects['aspect'].str.replace('#GENERAL', '', regex=False).str.replace('#', ' ', regex=False).str.title()
            chart = aspects.pivot_table(index="aspect", columns="sentiment", values="mentions", fill_value=0)
            st.bar_chart(chart, stack=True)
        else: st.info("No PhoBERT aspect results for this selection.")
        if not enriched.empty:
            st.bar_chart(enriched.overall_sentiment.value_counts())
        st.subheader("Recent reviews")
        if reviews.empty: st.caption("No reviews in this period.")
        for row in reviews.head(12).itertuples():
            with st.container(border=True):
                st.caption(f"{row.calendar_date} · Rating {row.rating if pd.notna(row.rating) else '—'}" +
                    (" · LIVE" if row.source == "live_web" else ""))
                st.write(row.review_text)
                if row.overall_sentiment == 'NOT_RELEVANT':
                    st.markdown('<span style="color:#aeb4be;background:#303640;padding:4px 8px;border-radius:6px">NOT RELEVANT</span>', unsafe_allow_html=True)
                elif pd.notna(row.absa_model_version): st.caption(str(row.overall_sentiment).title())
        st.divider()
        from src.ui.cross_chat_view import render_cross_chat
        with st.container(border=True,key="canonical_cross_chat_panel"):
            render_cross_chat(selected,start,end)
        st.divider(); st.header("Ask AI")
        st.caption("Ask AI Sales hiển thị SQL và kết quả đã kiểm chứng. Hỏi về review, PhoBERT hoặc phân tích kết hợp trong Cross Analysis phía trên.")
        with st.expander("Ask about sales"):
            from src.ui.sales_view import _ask_sql
            _ask_sql(selected, key_prefix="business_context_")
    except AnalyticsNotAccepted as error:
        st.error(str(error))
    except Exception:
        import logging
        logging.exception("Business analytics rendering failed")
        st.error("Analytics are temporarily unavailable. Check System Monitor and try Refresh.")
