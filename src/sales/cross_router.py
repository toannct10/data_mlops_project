"""Resolve analytical dimension before SQL generation; preserve accepted v4 routes."""
import re
from dataclasses import dataclass,asdict
from datetime import date,timedelta
from src.sales.semantic_plan import plain,parse_time_scope,next_month


@dataclass
class AnalyticalRoute:
    route:str
    metric:str
    dimension:str
    aggregation:str
    restaurant_ids:list
    start_date:str
    end_date:str
    limit:int=1
    ranking:bool=False
    negative:bool=False
    requires_sql:bool=True
    requires_retrieval:bool=False
    year_resolution:str='selected date range'
    follow_up:bool=False

    def as_dict(self):return asdict(self)


def route_cross(question,catalog,*,selected_id=None,start=None,end=None,state=None,today=None):
    from src.sales.text_to_sql import unsafe_request
    if unsafe_request(question):raise ValueError('Câu hỏi yêu cầu thao tác không được phép; chỉ hỗ trợ phân tích đọc dữ liệu.')
    today=today or date.today();state=state or {};q=plain(question)
    if not q.strip() or len(q)>2000:raise ValueError('Câu hỏi cần từ 1 đến 2.000 ký tự.')
    follow=bool(re.match(r'con |vay |what about|and |chu yeu ho|what are (?:its|their)',q) or re.search(r'nha hang do|cung thang|thang do',q))
    ids=[]
    for r in catalog:
        rid=str(r['restaurant_id']);full=plain(r['restaurant_name'] or '');name=full.split(' - ')[0]
        aliases={full} if len(full.split())>1 else set()
        if r.get('is_live_demo'):
            aliases.add(name);aliases.add(name.replace('ga ran ',''))
            if len(name.split()[0])>=5:aliases.add(name.split()[0])
        if any(a and re.search(r'\b'+re.escape(a)+r'\b',q) for a in aliases) or re.search(r'\b(?:id|quan|nha hang|restaurant)\s+'+re.escape(rid)+r'\b',q):ids.append(rid)
    latest=bool(re.search(r'moi nhat|latest|most recent',q))
    reviews=bool(re.search(r'review|danh gia|rating|phan nan|khach hang|complain|feedback|sentiment',q))
    sales=bool(re.search(r'doanh thu|revenue|sales|don hang|orders?|cancell|huy|aov',q))
    negative=bool(re.search(r'tieu cuc|review xau|danh gia xau|negative',q))
    ranking=bool(re.search(r'nhieu.+nhat|cao.+nhat|lon.+nhat|most |highest|greatest|top\s*\d+',q))
    month=bool(re.search(r'\bthang nao|which month|theo thang|monthly',q))
    restaurant=bool(re.search(r'nha hang nao|quan nao|which restaurant|top\s*\d+.*(?:restaurant|nha hang)',q))
    if re.search(r'\b(?:3|ba)\s+(?:quan|nha hang)|three restaurants',q):ids=[str(r['restaurant_id']) for r in catalog if r.get('is_live_demo')]
    if not ids:
        if follow:ids=list(state.get('restaurant_scope',[]))
        elif not restaurant and not latest:ids=[str(selected_id)] if selected_id else []
    from src.sales.question_scope import explicit_restaurants
    explicit=explicit_restaurants(question,catalog)
    if explicit:ids=explicit
    first,last=str(start or f'{today.year}-01-01'),str(end or today)
    if follow and len(state.get('period_scope',[]))==2:first,last=state['period_scope']
    temporal=q
    for number,name in enumerate(['january','february','march','april','may','june','july','august','september','october','november','december'],1):
        temporal=re.sub(r'\b'+name+r'\b',f'thang {number}',temporal)
    year_reason='explicit question' if re.search(r'20\d{2}',temporal) else 'selected date range'
    if re.search(r'\bthang\s+\d{1,2}\b',temporal) and not re.search(r'20\d{2}',temporal):
        years={first[:4],last[:4]};year=next(iter(years)) if len(years)==1 else str(today.year)
        temporal+=' nam '+year;year_reason='inherited scope year '+year if len(years)==1 else 'current calendar year explicitly resolved: '+year
    scope=parse_time_scope(temporal,today=today)
    if scope.periods:
        first=min(scope.periods)+'-01';last=str(date.fromisoformat(next_month(max(scope.periods))+'-01')-timedelta(days=1))
    if date.fromisoformat(first)>date.fromisoformat(last):raise ValueError('Khoảng ngày không hợp lệ.')
    metric='negative_review_count' if negative else 'review_count' if reviews else 'delivered_revenue'
    if negative and 'ty le' in q:metric='negative_review_rate'
    elif 'tich cuc' in q:metric='positive_review_count'
    if 'rating' in q or 'diem' in q or 'trung binh' in q and reviews:metric='average_rating_10'
    if 'huy' in q or 'cancel' in q:metric='cancellation_rate'
    if 'aov' in q or 'average order value' in q:metric='average_order_value'
    if re.search(r'bao nhieu don|how many orders|how many.*delivered',q):metric='delivered_orders'
    dimension='month' if month else 'restaurant' if restaurant else 'day' if re.search(r'theo ngay|daily',q) else 'scalar'
    if follow and dimension=='scalar':dimension=state.get('analytics_dimension') or dimension
    top=re.search(r'top\s*(\d+)',q);limit=min(int(top.group(1)),20) if top else 1 if ranking else 36 if dimension in {'month','day'} else 5 if restaurant else 1
    qualitative=reviews and not (negative or ranking or month or latest or re.search(r'bao nhieu|how many|diem|rating',q))
    if reviews and re.search(r'chu yeu|noi ve|phan nan dieu gi',q) and not ranking:qualitative=True
    # Keep the existing, grounded multi-aspect/association conversation intact.
    legacy=(re.search(r'xu huong|giong nhau|tuong quan|food quality|nhieu phan nan ve gia|trong 3 quan|so sanh 3 quan|quy 1|tai sao.*doanh thu thap nhat|chu yeu ho che',q)
            or follow and not state.get('analytics_route'))
    if not (sales or reviews or latest) and not (re.search(r'so sanh|compare',q) and len(ids)>1):legacy=True
    if legacy:return AnalyticalRoute('legacy','',dimension,'',ids,first,last,requires_sql=False,follow_up=follow)
    route='review_rag' if qualitative and not sales else 'cross_analysis' if sales and reviews else 'review_sql' if reviews else 'sales_sql'
    if latest:metric='latest_review' if reviews else 'latest_order';dimension='latest';route='review_sql' if reviews else 'sales_sql'
    if re.search(r'so sanh|compare',q) and len(ids)>1:route='cross_analysis'
    if route=='cross_analysis':dimension='restaurant';metric='sales_negative_reviews';limit=5
    return AnalyticalRoute(route,metric,dimension,'AVG' if metric=='average_rating_10' else 'COUNT' if metric.endswith('count') else 'SUM',list(dict.fromkeys(ids)),first,last,limit,ranking,negative,route!='review_rag',route in {'review_rag','cross_analysis'},year_reason,follow)
