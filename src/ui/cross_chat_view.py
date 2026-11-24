"""Conversational Cross Analysis inside the single Business Intelligence page."""
import os
import requests
import pandas as pd
import streamlit as st

def render_cross_chat(restaurant_id,start,end):
 st.header('Cross Analysis')
 st.caption('Kết hợp Sales, rating, PhoBERT và trích dẫn review từ cùng dữ liệu canonical. Câu hỏi có thể đổi nhà hàng/kỳ; câu hỏi tiếp nối kế thừa phạm vi trước đó.')
 key=(str(restaurant_id),str(start),str(end))
 if st.session_state.get('cross_chat_ui_scope')!=key:
  st.session_state['cross_chat_ui_scope']=key;st.session_state['cross_chat_messages']=[];st.session_state['cross_chat_state']={}
 if st.button('Bắt đầu cuộc trò chuyện mới',key='cross_chat_reset'):
  st.session_state['cross_chat_messages']=[];st.session_state['cross_chat_state']={}
 for message in st.session_state.get('cross_chat_messages',[]):
  with st.chat_message(message['role']):
   st.markdown(message['content'])
   if message.get('result'):show_evidence(message['result'])
 question=st.chat_input('Hỏi về doanh thu, review và các khía cạnh khách hàng...',key='canonical_cross_chat',max_chars=2000)
 if question:
  st.session_state['cross_chat_messages'].append({'role':'user','content':question})
  with st.chat_message('user'):st.markdown(question)
  with st.chat_message('assistant'):
   try:
    with st.status('Đang lập kế hoạch, đối chiếu dữ liệu và tạo câu trả lời...',expanded=False) as progress:
     base=os.getenv('FOODY_BACKEND_URL','http://rag-backend:8000' if os.path.exists('/.dockerenv') else 'http://localhost:8000')
     response=requests.post(base+'/cross-analysis',json={'question':question,'restaurant_id':str(restaurant_id),'start_date':str(start),'end_date':str(end),'conversation_state':st.session_state.get('cross_chat_state',{})},timeout=330)
     if response.status_code==422:st.info(response.json()['detail']);progress.update(label='Cần làm rõ phạm vi',state='complete');return
     response.raise_for_status();result=response.json();progress.update(label='Đã đối chiếu Sales và Review',state='complete')
    st.markdown(result['answer']);show_evidence(result)
    st.session_state['cross_chat_state']=result['conversation_state'];st.session_state['cross_chat_messages'].append({'role':'assistant','content':result['answer'],'result':result})
    st.session_state['cross_chat_messages']=st.session_state['cross_chat_messages'][-12:]
   except (requests.RequestException,ValueError):st.error('Chưa thể hoàn tất lượt phân tích. Hãy thử lại; dữ liệu không bị thay đổi.')

def show_evidence(result):
 if result.get('analysis'):
  analysis=result['analysis'];plan=analysis['plan']
  st.caption('Phạm vi: '+plan['start_date']+' → '+plan['end_date']+' · '+plan['year_resolution'])
  if analysis['supporting_metrics']:
   with st.expander('Supporting metrics',expanded=True):st.dataframe(pd.DataFrame(analysis['supporting_metrics']),hide_index=True)
  if analysis['review_evidence']:
   with st.expander('Review evidence'):
    for e in analysis['review_evidence']:
     st.caption(e['restaurant_name']+' · '+e['date']+' · Review ID '+e['review_id']);st.write(e['text'])
  st.caption('Source: '+str(analysis['freshness']['structured_source'] or 'RAG V3')+' · Query checked: '+str(analysis['freshness']['checked_at_utc']))
  if analysis['freshness'].get('data_timestamps'):st.caption('Data freshness: '+str(analysis['freshness']['data_timestamps']))
  if analysis['freshness'].get('review_source'):st.caption(analysis['freshness']['review_source'])
  details=result.get('sql_details')
  if details and details.get('sql'):
   with st.expander('Generated SQL' if details.get('generated') else 'Executed verified SQL'):
    st.code(details['sql'],language='sql');st.caption(details['backend']+' · '+details['execution_mode'])
  return
 packet=result['evidence_packet']
 with st.expander('Dữ liệu hỗ trợ và trích dẫn',expanded=False):
  scope=packet['plan'];st.caption('Nhà hàng: '+', '.join(packet['restaurant_names'][rid] for rid in scope['restaurant_ids'])+' · '+scope['start_date']+' → '+scope['end_date'])
  facts=[{'Ref':f['ref'],'Nhà hàng':packet['restaurant_names'][f['restaurant_id']],'Kỳ':f['period'],'Chỉ số':f['label'],'Giá trị':f['display']} for f in packet['facts']]
  st.dataframe(pd.DataFrame(facts),hide_index=True,use_container_width=True)
  for evidence in packet['retrieved_reviews']:
   st.markdown('**['+evidence['ref']+'] '+packet['restaurant_names'][evidence['restaurant_id']]+' · '+evidence['period']+'**')
   st.write(evidence['text']);st.caption('Review ID: '+evidence['review_id'])
  for limitation in packet['data_limitations']:st.caption(limitation)
  st.caption('Chế độ trả lời: '+result['answer_mode']+' · Nguồn: canonical PostgreSQL + PhoBERT + Review retrieval')
