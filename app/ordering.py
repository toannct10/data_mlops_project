"""Customer-facing three-restaurant Ordering Web for the local thesis demo."""

import os
from html import escape
from decimal import Decimal

import requests
import streamlit as st

from src.ui.ordering_styles import apply_ordering_styles

BASE = os.getenv("FOODY_ORDERING_API_URL", "http://rag-backend:8000").rstrip("/")


def api_call(method, path, *, payload=None):
    try:
        response = requests.request(method, BASE + path, json=payload, timeout=90)
        if response.status_code >= 400:
            detail = response.json().get("detail", "Request could not be completed")
            raise ValueError(detail if response.status_code < 500 else
                             "Ordering service is temporarily unavailable")
        return response.json()
    except requests.RequestException as error:
        raise ValueError("Ordering service is temporarily unavailable") from error


def money(value):
    return f"{Decimal(str(value)):,.0f} ₫"


def _variant_label(value):
    return "Phần tiêu chuẩn" if str(value).upper() == "DEFAULT" else str(value)


def _cart():
    return st.session_state.setdefault("ordering_cart", {})


def _identity(restaurant_id):
    return {"2642": ("🍗", "Gà rán · Fast Food"), "1803": ("🧋", "Trà trái cây · Trà sữa"),
            "145": ("☕", "Cà phê · Đồ uống")}.get(str(restaurant_id), ("🍽️", "Món ngon mỗi ngày"))


def _restaurant_cards(restaurants):
    st.title("Đặt món hôm nay")
    st.caption("3 nhà hàng đang hỗ trợ đặt món trực tiếp")
    search = st.text_input("Tìm nhà hàng", placeholder="Bạn muốn ăn gì hôm nay?")
    columns = st.columns(3)
    for i, restaurant in enumerate(restaurants):
        if search.casefold() not in restaurant['restaurant_name'].casefold(): continue
        with columns[i % 3], st.container(border=True):
            icon, category = _identity(restaurant['restaurant_id'])
            st.markdown(f'<div class="food-art">{icon}</div><span class="live-pill">LIVE</span>', unsafe_allow_html=True)
            st.subheader(restaurant['restaurant_name'])
            st.caption(category)
            menu_count = len(api_call("GET", f"/ordering/restaurants/{restaurant['restaurant_id']}/menu"))
            st.caption(f"{menu_count} món trong thực đơn")
            if st.button("Xem thực đơn →", key="choose_" + restaurant["restaurant_id"], use_container_width=True):
                st.session_state["ordering_restaurant"] = restaurant["restaurant_id"]
                st.session_state["ordering_cart"] = {}
                st.session_state.pop("ordering_order_id", None)
                st.session_state.pop("ordering_review_result", None)
                st.rerun()


def _menu(restaurant_id, restaurant_name):
    if st.button("← Nhà hàng"):
        st.session_state.pop("ordering_restaurant", None)
        st.session_state["ordering_cart"] = {}
        st.query_params.clear()
        st.rerun()
    icon, category = _identity(restaurant_id)
    st.title(restaurant_name)
    st.caption(category + " · LIVE ORDERING")
    items = api_call("GET", f"/ordering/restaurants/{restaurant_id}/menu")
    search = st.text_input("Tìm món", placeholder="Tìm món yêu thích…")
    categories = list(dict.fromkeys(item['category'] for item in items))
    for category in categories:
        matching = [item for item in items if item['category'] == category and search.casefold() in item['item_name'].casefold()]
        if not matching: continue
        st.subheader(category)
        columns = st.columns(2)
        for i, item in enumerate(matching):
            with columns[i % 2], st.container(border=True):
                st.markdown(f'<div class="food-art small">{icon}</div>', unsafe_allow_html=True)
                st.markdown(f"**{item['item_name']}**")
                variants = [v for v in item['variants'] if v['variant_available']]
                if not item['is_available'] or not variants:
                    st.caption("Tạm hết món"); continue
                variant = st.selectbox("Chọn phần", variants,
                    format_func=lambda v: _variant_label(v['variant_name']) + " · " + money(v['unit_price']),
                    key='variant_' + item['menu_item_id'])
                if st.button("+ Thêm", key='add_' + variant['variant_id'], use_container_width=True):
                    cart = _cart(); key = variant['variant_id']
                    if key in cart: cart[key]['quantity'] = min(99, cart[key]['quantity'] + 1)
                    else:
                        cart[key] = {'variant_id': key, 'item_name': item['item_name'],
                            'variant_name': variant['variant_name'], 'unit_price': variant['unit_price'], 'quantity': 1}
                    st.rerun()


def _cart_view(restaurant_id):
    st.subheader("Giỏ hàng của bạn")
    cart = _cart()
    if not cart:
        st.caption("Chọn món ngon để bắt đầu nhé.")
        return
    total = Decimal(0)
    for key, line in list(cart.items()):
        price = Decimal(str(line["unit_price"]))
        subtotal = price * line["quantity"]
        total += subtotal
        st.write(f"{line['item_name']} · {_variant_label(line['variant_name'])} — "
                 f"{line['quantity']} × {money(price)} = {money(subtotal)}")
        minus, plus, remove = st.columns(3)
        if minus.button("−", key="minus_" + key, disabled=line["quantity"] <= 1):
            line["quantity"] -= 1
            st.rerun()
        if plus.button("+", key="plus_" + key, disabled=line["quantity"] >= 99):
            line["quantity"] += 1
            st.rerun()
        if remove.button("Remove", key="remove_" + key):
            del cart[key]
            st.rerun()
    st.divider()
    st.metric("Tổng cộng", money(total))
    st.markdown(f'<a class="mobile-cart" href="#checkout">Giỏ hàng · {sum(line["quantity"] for line in cart.values())} món · {money(total)}</a>', unsafe_allow_html=True)
    st.markdown('<div id="checkout"></div>', unsafe_allow_html=True)
    st.subheader("Thông tin giao hàng")
    with st.form("checkout"):
        name = st.text_input("Họ và tên")
        phone = st.text_input("Số điện thoại")
        address = st.text_area("Địa chỉ giao hàng", height=80)
        st.caption("Thanh toán khi nhận hàng · Đơn thử nghiệm, không thu tiền. Thông tin giao hàng chỉ dùng trên màn hình này.")
        submit = st.form_submit_button("Đặt đơn", type="primary", use_container_width=True)
    if submit:
        if not all(x.strip() for x in (name, phone, address)):
            st.warning("Vui lòng điền đầy đủ thông tin giao hàng."); return
        result = api_call("POST", "/ordering/orders", payload={
            "restaurant_id": restaurant_id,
            "lines": [{"variant_id": line["variant_id"], "quantity": line["quantity"]} for line in cart.values()]})
        st.session_state["ordering_order_id"] = result["order_id"]
        st.session_state["ordering_cart"] = {}
        st.session_state.pop("ordering_review_result", None)
        st.rerun()


def _track_order(order_id):
    order = api_call("GET", f"/ordering/orders/{order_id}")
    st.subheader("Track your order")
    st.caption(f"Đơn #{order_id} · {money(order['total_amount'])}")
    stages = [("PENDING", "Đã đặt đơn"), ("PREPARING", "Đang chuẩn bị"),
              ("DELIVERING", "Đang giao"), ("DELIVERED", "Đã giao")]
    if order['status'] == 'CANCELLED': st.warning("Đơn hàng đã huỷ")
    else:
        current = [k for k, _ in stages].index(order['status'])
        for i, (_, label) in enumerate(stages):
            st.write(("✓ " if i < current else "● " if i == current else "○ ") + label)
    reviewed = st.session_state.get("ordering_review_result")
    if order["status"] == "DELIVERED" and not (
            reviewed and reviewed.get("order_id") == order_id):
        st.success("✓ Giao hàng thành công · Bạn thấy món ăn thế nào?")
        with st.form("order_review"):
            rating = st.select_slider("Rating", options=[1, 2, 3, 4, 5], value=5)
            comment = st.text_area("Your review", height=130)
            submitted = st.form_submit_button("Submit review")
        if submitted:
            if not comment.strip():
                st.warning("Please enter your review before submitting.")
            else:
                result = api_call("POST", f"/ordering/orders/{order_id}/review",
                                  payload={"rating": rating, "comment": comment})
                st.session_state["ordering_review_result"] = result
                st.rerun()
    if reviewed and reviewed.get("order_id") == order_id:
        result = reviewed
        st.markdown("#### AI Review Analysis")
        if result['overall_sentiment'] == 'NOT_RELEVANT':
            st.info('NOT RELEVANT — This review is not related to restaurant or dining experience.')
            st.caption('PhoBERT ABSA skipped because the review was classified as not relevant.')
            return
        st.metric("Overall", result["overall_sentiment"].title())
        if result["reason"]:
            st.caption(result["reason"])
        for aspect in result["aspects"]:
            label = aspect['label'].replace('#GENERAL', '').replace('#', ' ').replace('&', ' & ').title()
            st.markdown(f'<div class="aspect-result"><strong>{escape(label)}</strong><span>{escape(aspect["sentiment"].title())}</span></div>', unsafe_allow_html=True)



def _demo_controls(restaurant_id):
    with st.expander("Demo Controls", expanded=False):
        st.caption("Advance or cancel a live order to demonstrate revenue rules.")
        for order in api_call("GET", f"/ordering/orders?restaurant_id={restaurant_id}")[:12]:
            st.write(f"{order['order_id']} · {order['status']} · "
                     f"{money(order['total_amount'])}")
            if order["status"] in {"PENDING", "PREPARING", "DELIVERING"}:
                left, right = st.columns(2)
                if left.button("Advance status", key="advance_" + order["order_id"]):
                    api_call("POST", f"/ordering/orders/{order['order_id']}/advance")
                    st.rerun()
                if right.button("Cancel order", key="cancel_" + order["order_id"]):
                    api_call("POST", f"/ordering/orders/{order['order_id']}/cancel")
                    st.rerun()


def main():
    st.set_page_config(page_title="Foody Live · Đặt món", page_icon="🍽️", layout="wide")
    apply_ordering_styles()
    from src.portable.banner import show
    show()
    count = sum(line['quantity'] for line in _cart().values())
    st.markdown(f'<div class="brand-bar"><strong>Foody Live<span> / món ngon, mỗi ngày</span></strong><span>Giỏ hàng · {count}</span></div>', unsafe_allow_html=True)
    try:
        restaurants = api_call("GET", "/ordering/restaurants")
        by_id = {item['restaurant_id']: item['restaurant_name'] for item in restaurants}
        if 'ordering_restaurant' not in st.session_state and st.query_params.get('restaurant') in by_id:
            st.session_state['ordering_restaurant'] = st.query_params['restaurant']
        restaurant_id = st.session_state.get('ordering_restaurant')
        if restaurant_id is None: _restaurant_cards(restaurants)
        elif restaurant_id in by_id:
            if order_id := st.session_state.get('ordering_order_id'):
                _track_order(order_id)
                if st.button("Đặt thêm món"):
                    st.session_state.pop('ordering_order_id', None)
                    st.rerun()
            else:
                menu, cart = st.columns((2.2, 1), gap='large')
                with menu: _menu(restaurant_id, by_id[restaurant_id])
                with cart, st.container(border=True, key='cart_panel'): _cart_view(restaurant_id)
            _demo_controls(restaurant_id)
        else:
            st.session_state.pop('ordering_restaurant', None); st.rerun()
    except ValueError as error: st.error(str(error))


if __name__ == '__main__': main()
