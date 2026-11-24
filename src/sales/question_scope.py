"""Resolve explicit restaurant entities before invoking bounded analytical services."""
import re
from src.sales.semantic_plan import plain

def explicit_restaurants(question,catalog):
    q=plain(question);known={str(r['restaurant_id']) for r in catalog};ids=[]
    requested=re.findall(r'\b(?:nha hang|restaurant|id|ma)\s+(\d+)\b',q)
    unknown=[r for r in requested if r not in known]
    if unknown:raise ValueError('Không tìm thấy nhà hàng có mã '+', '.join(unknown)+'.')
    tokens=re.findall(r'\b[a-z]{2,}\d+[a-z0-9]*\b',q)
    if tokens and re.search(r'nha hang|khong ton tai|restaurant',q):
        for token in tokens:
            if not any(token in plain(r['restaurant_name'] or '') for r in catalog):
                raise ValueError('Không tìm thấy nhà hàng có mã '+token.upper()+'.')
    for r in catalog:
        rid=str(r['restaurant_id']);full=plain(r['restaurant_name'] or '')
        aliases={full} if len(full.split())>1 else set()
        if r.get('is_live_demo'):
            prefix=full.split(' - ')[0];aliases.update({prefix,prefix.replace('ga ran ','')})
            if prefix and len(prefix.split()[0])>=5:aliases.add(prefix.split()[0])
        if rid in requested or any(a and re.search(r'\b'+re.escape(a)+r'\b',q) for a in aliases):ids.append(rid)
    return list(dict.fromkeys(ids))
