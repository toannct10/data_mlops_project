import os

def show():
    if os.getenv('FOODY_PORTABLE')!='true':return
    import streamlit as st
    if os.getenv('FOODY_PORTABLE_MODE','local')=='local':
        st.info('LOCAL DEMO — Cloud synchronization disabled.')
    else:
        st.info('CLOUD DEMO — External pipeline explicitly enabled. Sales uses local canonical PostgreSQL; Cross SQL identifies its actual source.')
