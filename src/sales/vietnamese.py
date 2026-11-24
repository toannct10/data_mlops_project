"""Deterministic Vietnamese verbalization of already verified facts; no model/data access."""
from datetime import date
from decimal import Decimal
import re

LABELS={'delivered_sales':'doanh thu từ đơn giao thành công','delivered_revenue':'doanh thu từ đơn giao thành công',
 'total_orders':'tổng số đơn hàng','order_count':'tổng số đơn hàng','delivered_count':'số đơn giao thành công','delivered_orders':'số đơn giao thành công',
 'cancelled_count':'số đơn bị hủy','cancelled_orders':'số đơn bị hủy','refunded_count':'số đơn hoàn tiền',
 'average_order_value':'giá trị trung bình mỗi đơn giao thành công','aov':'giá trị trung bình mỗi đơn giao thành công',
 'negative_review_count':'số đánh giá tiêu cực','negative_reviews':'số đánh giá tiêu cực',
 'positive_review_count':'số đánh giá tích cực','positive_reviews':'số đánh giá tích cực',
 'review_count':'số đánh giá','eligible_review_count':'số đánh giá đủ điều kiện phân tích cảm xúc','eligible_reviews':'số đánh giá đủ điều kiện phân tích cảm xúc',
 'negative_review_rate':'tỷ lệ đánh giá tiêu cực','negative_rate':'tỷ lệ đánh giá tiêu cực','cancellation_rate':'tỷ lệ hủy đơn','refund_rate':'tỷ lệ hoàn đơn',
 'average_rating_10':'điểm đánh giá trung bình','rating_10':'điểm đánh giá trung bình',
 'gross_value':'tổng giá trị danh nghĩa của các đơn','cancelled_value':'giá trị đơn bị hủy',
 'food_complaints':'số đánh giá chê chất lượng món ăn','service_complaints':'số đánh giá chê dịch vụ',
 'previous_revenue':'doanh thu tháng trước','previous_negative_reviews':'số đánh giá tiêu cực tháng trước',
 'first_revenue':'doanh thu tháng đầu','second_revenue':'doanh thu tháng sau','revenue_change':'mức tăng doanh thu',
 'first_negative_rate':'tỷ lệ tiêu cực tháng đầu','second_negative_rate':'tỷ lệ tiêu cực tháng sau'}
MONEY={'delivered_sales','delivered_revenue','aov','average_order_value','gross_value','cancelled_value','first_revenue','second_revenue','previous_revenue','revenue_change'}
RATES={'negative_review_rate','negative_rate','cancellation_rate','refund_rate','first_negative_rate','second_negative_rate'}

def number(value,decimals=0):
    return f'{Decimal(str(value)):,.{decimals}f}'.replace(',','_').replace('.',',').replace('_','.')

def value_text(key,value):
    if value is None:return 'chưa đủ dữ liệu'
    if key in MONEY:return number(value)+' đồng'
    if key in RATES:return number(Decimal(str(value))*100,2)+'%'
    if key in {'average_rating_10','rating_10'}:return number(value,2)+'/10'
    return number(value) if isinstance(value,(int,float,Decimal)) else str(value)

def month(value):
    text=str(value)
    return f'tháng {int(text[5:7])}/{text[:4]}' if re.match(r'^20\d{2}-\d{2}',text) else text

def clean(text):
    # Only translate internal presentation terms; retain quoted/customer text elsewhere.
    text=re.sub(r'\bdoanh thu kỳ\b','doanh thu trong thời gian được hỏi',text,flags=re.I)
    text=re.sub(r'\bDelivered\b','giao thành công',text,flags=re.I)
    text=text.replace('doanh thu giao thành công','doanh thu từ đơn giao thành công').replace('AOV','giá trị trung bình mỗi đơn giao thành công')
    return text

def rows_text(rows,*,restaurant_name=None,scope=None,metrics=None):
    if not rows:return 'Không có bản ghi phù hợp trong phạm vi được hỏi; không suy diễn giá trị cho thời gian thiếu dữ liệu.'
    lines=[]
    for row in rows:
        period=row.get('period',row.get('period_start'));name=row.get('restaurant_name') or restaurant_name
        lead=('Trong '+month(period)+', ' if period else '')+(name if name else 'Toàn bộ phạm vi được hỏi')
        if row.get('restaurant_id'):lead+=' (mã '+str(row['restaurant_id'])+')'
        if row.get('first_period') and row.get('second_period'):lead+='; so sánh '+month(row['first_period'])+' với '+month(row['second_period'])
        facts=[LABELS[k]+' là '+value_text(k,v) for k,v in row.items() if k in LABELS and (not metrics or k in metrics)]
        lines.append(lead+': '+('; '.join(facts) if facts else 'chưa có chỉ số đủ điều kiện hiển thị')+'.')
    text='\n\n'.join(lines)
    if scope:text+='\n\nPhạm vi dữ liệu: '+scope+'.'
    return text
