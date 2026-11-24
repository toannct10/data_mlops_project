"""Original consumer ordering visual identity; no commercial artwork."""
import streamlit as st


def apply_ordering_styles():
    st.markdown("""<style>
    :root{color-scheme:light}.stApp{background:#f7f6f2;color:#28352f;font-family:Arial,sans-serif}
    [data-testid='stHeader']{background:#f7f6f2}[data-testid='stToolbar']{display:none}.block-container{max-width:1360px;padding:4.5rem 2rem 5rem}
    h1,h2,h3,p,label{color:#28352f}h1{font-size:32px!important;letter-spacing:-1px}
    h2{font-size:24px!important}h3{font-size:20px!important}
    .brand-bar{display:flex;justify-content:space-between;gap:12px;padding:18px 0 26px;border-bottom:1px solid #e5e5df;margin-bottom:28px;color:#24604a}
    .brand-bar strong{font-size:25px}.brand-bar span{font-size:14px;font-weight:400}
    .food-art{display:flex;align-items:center;justify-content:center;font-size:88px;height:190px;border-radius:18px;background:linear-gradient(135deg,#e8f0df,#f8ddbe);margin-bottom:18px}
    .food-art.small{height:110px;font-size:58px}
    .aspect-result{display:flex;justify-content:space-between;padding:12px 16px;border-radius:12px;background:#eaf2e9;margin:6px 0}.live-pill{background:#e0f0e5;color:#286347;border-radius:20px;padding:4px 10px;font-size:11px;font-weight:700;letter-spacing:1px}
    [data-testid='stVerticalBlockBorderWrapper']{background:#fff;border:1px solid #e9e8e1!important;border-radius:20px;box-shadow:0 4px 18px #253c2e08}
    [data-testid='stMetricValue']{color:#244f3e;font-variant-numeric:tabular-nums;font-weight:700}
    [data-testid='stWidgetLabel'] p{color:#59645d;font-size:13px}
    [data-baseweb='select']>div,input,textarea{background:#fff!important;color:#28352f!important}
    [data-baseweb='select'] *{color:#28352f}
    button[kind='primary']{background:#28634e!important;color:white!important;border-color:#28634e!important}
    button{border-radius:12px!important;background:#fff!important;color:#28634e!important}button p{color:inherit!important}button[kind='primary'] p{color:white!important}button:hover{border-color:#b97549!important}
    .st-key-cart_panel{position:sticky;top:30px}
    .mobile-cart{display:none}
    @media(max-width:760px){.block-container{padding:4.5rem 1rem 6rem}
    [data-testid='stHorizontalBlock']{flex-wrap:wrap}
    [data-testid='stColumn']{width:100%!important;flex:1 1 100%!important;min-width:0!important}
    .brand-bar strong span{display:none}.food-art{height:150px}
    .st-key-cart_panel{position:static}
    .mobile-cart{display:block;position:fixed;bottom:12px;left:16px;right:16px;z-index:99;background:#28634e;color:white!important;border-radius:14px;padding:14px;text-align:center;text-decoration:none;box-shadow:0 4px 20px #0002}}
    </style>""", unsafe_allow_html=True)
