"""Current architecture and observed cloud pipeline health."""
from html import escape
import streamlit as st

@st.cache_data(ttl=15, show_spinner=False)
def _pipeline_status():
    from src.monitoring.cloud_pipeline import cloud_snapshot
    from src.monitoring.operations import outbox_snapshot
    return {**cloud_snapshot(), 'outbox':outbox_snapshot()}

def render_sidebar_details():
    st.sidebar.markdown("<div class='foody-eyebrow'>System status</div>", unsafe_allow_html=True)
    pipeline=_pipeline_status()
    label=pipeline['status']
    tone='good' if label=='HEALTHY' else 'danger' if label=='ERROR' else 'warn'
    stamp=pipeline.get('last_success_utc')
    updated=escape(str(stamp).replace('T',' ')) if stamp else 'Not yet observed'
    pending=pipeline['outbox']['pending']
    st.sidebar.markdown(
        '<div class="foody-sidebar-card"><strong>Runtime</strong><br>'
        'Operational Database · PostgreSQL<br>Analytics Warehouse · Snowflake<br>'
        'Sales AI · Text-to-SQL<br>Cross AI · SQL + RAG + PhoBERT<br>LLM · Qwen2.5-3B<br>'
        'Pipeline · Airflow → S3 → Snowflake → dbt<br>'
        f'<span class="foody-badge {tone}">{escape(label)}</span><br>'
        f'Pending outbox events · {pending if pending is not None else "Unknown"}<br>'
        f'<span class="foody-sidebar-muted">Last successful cloud update: {updated} UTC</span></div>',
        unsafe_allow_html=True)
    with st.sidebar.expander('Pipeline observations'):
        for stage,record in pipeline['stages'].items():
            st.caption(stage+' · '+record['status']+' · '+str(record.get('observed_at_utc','Not yet observed')))
        st.caption('Data freshness and successful queries are tracked separately. PostgreSQL fallback is labeled in Cross answers.')
