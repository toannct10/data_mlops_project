"""Start BI metrics with the server, before any browser session connects."""
import os
from src.monitoring.metrics import start_streamlit_metrics

def main():
    from streamlit.web import cli
    start_streamlit_metrics(int(os.getenv('FOODY_STREAMLIT_METRICS_PORT','8002')))
    cli.main(args=['run','app/app.py','--server.address','0.0.0.0','--server.port','8501'])

if __name__=='__main__':main()
