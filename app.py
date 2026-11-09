import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
import feedparser

st.set_page_config(page_title='個股研究室',page_icon='📈',layout='wide')
st.markdown('''<style>div[data-testid="stMetricDelta"] [data-testid="stMetricDeltaIcon"]{display:none} .block-container{padding-top:1.5rem} h1,h2,h3{color:#252b36}</style>''',unsafe_allow_html=True)
TZ=timezone(timedelta(hours=8))

def tickers(raw):
    s=raw.strip().upper()
    if re.fullmatch(r'\d{4,6}',s): return [s+'.TW',s+'.TWO']
    if re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,12}',s): return [s]
    return []

@st.cache_data(ttl=900,show_spinner=False)
def history(symbol,period='5y'):
    df=yf.Ticker(symbol).history(period=period,auto_adjust=False)
    if df.empty:return df
    df=df.sort_index()
    df.index=pd.to_datetime(df.index).tz_localize(None) if df.index.tz is None else pd.to_datetime(df.index).tz_localize(None)
    return df

@st.cache_data(ttl=86400,show_spinner=False)
def company(symbol):
    try:return yf.Ticker(symbol).info or {}
    except Exception:return {}

@st.cache_data(ttl=21600,show_spinner=False)
def statements(symbol):
    t=yf.Ticker(symbol)
    def get(attr):
        try:return getattr(t,attr)
        except Exception:return pd.DataFrame()
    return get('quarterly_income_stmt'),get('quarterly_balance_sheet'),get('quarterly_cashflow')

def resolve(raw):
    for symbol in tickers(raw):
        try:
            h=history(symbol,'3mo')
            if not h.empty:return symbol
        except Exception:pass
    return None

def rowval(df,names,col):
    if df.empty or col not in df.columns:return np.nan
    for name in names:
        if name in df.index:
            try:return float(df.loc[name,col])
            except Exception:pass
    return np.nan

def quarter_data(symbol):
    inc,bal,cash=statements(symbol)
    if inc.empty:return pd.DataFrame(), '損益表未提供'
    cols=sorted(pd.to_datetime(inc.columns),reverse=True)[:3]
    records=[]
    for i,c in enumerate(cols[:2]):
        bc=min(bal.columns,key=lambda x:abs((pd.Timestamp(x)-c).days)) if not bal.empty else None
        cc=min(cash.columns,key=lambda x:abs((pd.Timestamp(x)-c).days)) if not cash.empty else None
        revenue=rowval(inc,['Total Revenue','Operating Revenue'],c)
        gross=rowval(inc,['Gross Profit'],c)
        net=rowval(inc,['Net Income Common Stockholders','Net Income'],c)
        eps=rowval(inc,['Diluted EPS','Basic EPS'],c)
        assets=rowval(bal,['Total Assets'],bc) if bc is not None else np.nan
        liabilities=rowval(bal,['Total Liabilities Net Minority Interest','Total Liabilities'],bc) if bc is not None else np.nan
        equity=rowval(bal,['Stockholders Equity','Common Stock Equity'],bc) if bc is not None else np.nan
        prevcols=sorted(bal.columns,reverse=True) if not bal.empty else []
        prev_equity=np.nan
        if bc in prevcols and prevcols.index(bc)+1<len(prevcols):
            prev_equity=rowval(bal,['Stockholders Equity','Common Stock Equity'],prevcols[prevcols.index(bc)+1])
        op=rowval(cash,['Operating Cash Flow','Cash Flow From Continuing Operating Activities'],cc) if cc is not None else np.nan
        cap=rowval(cash,['Capital Expenditure'],cc) if cc is not None else np.nan
        # Yahoo quarterly_cashflow is normally discrete quarters, but cumulative YTD data can occur.
        # Do not infer single-quarter cash flows if statement basis is unverified.
        records.append(dict(季度=c.strftime('%Y-%m-%d'),營收=revenue,毛利率=gross/revenue*100 if pd.notna(gross) and revenue else np.nan,
            EPS=eps,負債比率=liabilities/assets*100 if pd.notna(liabilities) and assets else np.nan,
            ROE=net/((equity+prev_equity)/2)*100 if pd.notna(net) and pd.notna(equity) and pd.notna(prev_equity) and equity+prev_equity else np.nan,
            營業現金流=op,自由現金流=op+cap if pd.notna(op) and pd.notna(cap) else np.nan))
    df=pd.DataFrame(records).set_index('季度')
    if len(cols)>1:
        previous=rowval(inc,['Total Revenue','Operating Revenue'],cols[1])
        df.loc[df.index[0],'營收成長率QoQ']=(df.iloc[0]['營收']/previous-1)*100 if pd.notna(previous) and previous else np.nan
    if len(cols)>2:
        previous=rowval(inc,['Total Revenue','Operating Revenue'],cols[2])
        df.loc[df.index[1],'營收成長率QoQ']=(df.iloc[1]['營收']/previous-1)*100 if pd.notna(previous) and previous else np.nan
    return df, '季度以財報期間截止日標示；現金流數據以供應商季度欄位為準，請與原始申報核對累計/單季口徑。'

def fmt(x,kind):
    if pd.isna(x):return 'N/A'
    if kind=='pct':return f'{x:,.2f}%'
    if kind=='eps':return f'{x:,.2f}'
    return f'{x:,.0f}'

@st.cache_data(ttl=3600,show_spinner=False)
def news(symbol,name):
    import requests
    from xml.etree import ElementTree as ET
    query=f'"{name}" stock' if name and len(name)>3 else f'"{symbol.split(".")[0]}" stock'
    url='https://news.google.com/rss/search?q='+quote(query)+'&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
    try:
        resp=requests.get(url,timeout=12,headers={'User-Agent':'Mozilla/5.0'});resp.raise_for_status()
        root=ET.fromstring(resp.content);out=[]
        from email.utils import parsedate_to_datetime
        for item in root.findall('.//item'):
            title=item.findtext('title','');link=item.findtext('link','')
            pub=item.findtext('pubDate','');source=item.findtext('source','Google News')
            try:dt=parsedate_to_datetime(pub).astimezone(TZ)
            except Exception:continue
            # Require company name or exact symbol token to avoid similarly named businesses.
            if name and name.lower() not in title.lower() and not re.search(r'(?<![A-Z0-9])'+re.escape(symbol.split('.')[0])+r'(?![A-Z0-9])',title,re.I):continue
            out.append((dt,title,source,link))
        return sorted(out,reverse=True)
    except Exception:return []

st.title('📈 個股研究室')
st.caption('台灣上市、上櫃與美國股票｜免費資料可能延遲，所有數字應以原始公告為準')
raw=st.text_input('輸入股票代號',value='2330',placeholder='例如 2330、6488、NVDA')
symbol=resolve(raw)
if not symbol:
    st.error('查無有效股票代號或目前無法取得行情。請檢查代號與網路連線。');st.stop()
info=company(symbol)
name=info.get('longName') or info.get('shortName') or symbol
st.subheader(f'{name} · {symbol}')
ccy=info.get('financialCurrency') or '未提供'
cols=st.columns(4)
for col,(label,value) in zip(cols,[('產業',info.get('industry') or 'N/A'),('市值',f"{info['marketCap']:,.0f}" if info.get('marketCap') is not None else 'N/A'),('本益比',f"{info['trailingPE']:.2f}" if isinstance(info.get('trailingPE'),(int,float)) else 'N/A'),('財報幣別',ccy)]):col.metric(label,value)
st.caption('公司基本資料：Yahoo Finance；市值以報價幣別計。本益比為過去12個月口徑。')
summary=info.get('longBusinessSummary')
if summary:st.write('**業務介紹（英文原文；尚未取得可靠繁中翻譯）**：'+summary[:850]+('…' if len(summary)>850 else ''))
else:st.info('業務介紹暫無資料。')

st.header('股價技術圖表')
period=st.radio('觀察期間',['3個月','6個月','1年','2年','5年'],index=2,horizontal=True)
allh=history(symbol,'5y')
if allh.empty:st.warning('無法取得股價資料')
else:
    for n in (20,60,120):allh[f'MA{n}']=allh.Close.rolling(n,min_periods=n).mean()
    months={'3個月':3,'6個月':6,'1年':12,'2年':24,'5年':60}[period]
    start=allh.index.max()-pd.DateOffset(months=months)
    h=allh.loc[allh.index>=start]
    fig=go.Figure(go.Candlestick(x=h.index,open=h.Open,high=h.High,low=h.Low,close=h.Close,name='K線',increasing_line_color='#d43b42',decreasing_line_color='#16965e'))
    for n,color in [(20,'#d19a2a'),(60,'#3386c7'),(120,'#875ac4')]:fig.add_trace(go.Scatter(x=h.index,y=h[f'MA{n}'],name=f'MA{n}',line=dict(color=color,width=1.6)))
    fig.update_layout(height=490,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=20,b=5),legend=dict(orientation='h'))
    st.plotly_chart(fig,use_container_width=True)
    st.caption(f'行情來源：Yahoo Finance｜最新交易日：{h.index.max().date()}｜紅漲綠跌｜均線依實際交易日收盤價計算，未滿天數不顯示。')

st.header('最近兩季財報比較')
q,warning=quarter_data(symbol)
if q.empty:st.warning(warning)
else:
    st.caption(f'財報幣別：{ccy}；金額單位：元（原始報表單位）；EPS：每股 {ccy}；百分比為 %。來源：Yahoo Finance 財報欄位。')
    fields=[('營收','money'),('營收成長率QoQ','pct'),('毛利率','pct'),('EPS','eps'),('負債比率','pct'),('ROE','pct'),('營業現金流','money'),('自由現金流','money')]
    display=pd.DataFrame({d:{field:fmt(q.loc[d,field],kind) for field,kind in fields} for d in q.index})
    st.dataframe(display,use_container_width=True)
    field=st.selectbox('柱狀圖比較指標',[f for f,_ in fields])
    fig=go.Figure(go.Bar(x=list(q.index),y=q[field].tolist(),marker_color=['#b72f3c','#76879b'][:len(q)]))
    fig.update_layout(height=300,yaxis_title='%' if field in ['營收成長率QoQ','毛利率','負債比率','ROE'] else ccy)
    st.plotly_chart(fig,use_container_width=True)
    st.caption('QoQ＝本季營收/前季營收−1；毛利率＝毛利/營收；負債比率＝總負債/總資產；ROE＝本季淨利/期初期末平均權益（未年化）；自由現金流＝營業現金流＋資本支出（資本支出以負值記錄）。')
    st.warning(warning+' 若供應商的現金流為年初累計值，應先扣除前期累計值；未核實前不應視為已驗證的單季現金流。')

st.header('近期重要事件與公司動態')
items=news(symbol,name)
now=datetime.now(TZ)
recent=[x for x in items if now-timedelta(days=30)<=x[0]<=now+timedelta(days=1)]
if not recent:st.info('最近30天未取得符合公司名稱核對條件的新聞，或新聞來源暫時不可用。')
for dt,title,source,link in recent[:30]:
    label='近7天' if dt>=now-timedelta(days=7) else '近30天'
    st.markdown(f'**{label}｜{dt:%Y-%m-%d}｜{source}**  [{title}]({link})')
st.caption('新聞來源：Google News RSS；僅顯示標題符合公司名稱或股票代號的結果。新聞可能遺漏、連結可能導向新聞聚合頁；正式公告請核對 MOPS 或 SEC EDGAR。')

st.header('股票比較（最多五檔）')
comparison=st.text_input('輸入股票代號，以逗號分隔',value='2330,NVDA,MSFT')
period2=st.selectbox('比較期間',['3個月','6個月','1年','2年','5年'],index=2)
inputs=[x.strip() for x in re.split(r'[,，;；\s]+',comparison) if x.strip()]
if len(inputs)>5:st.error('最多只能輸入五檔股票。')
else:
    series={};errors=[]
    for raw_symbol in dict.fromkeys(inputs):
        sy=resolve(raw_symbol)
        if not sy:errors.append(raw_symbol);continue
        hh=history(sy,'5y')
        if hh.empty:errors.append(raw_symbol);continue
        start=hh.index.max()-pd.DateOffset(months={'3個月':3,'6個月':6,'1年':12,'2年':24,'5年':60}[period2])
        series[sy]=hh.loc[hh.index>=start,'Close']
    if errors:st.warning('無法取得：'+', '.join(errors))
    if series:
        aligned=pd.concat(series,axis=1).sort_index().ffill().dropna()
        if aligned.empty:st.warning('沒有可共同對齊的交易日。')
        else:
            normalized=aligned.div(aligned.iloc[0]).mul(100)
            fig=go.Figure()
            for sy in normalized:fig.add_trace(go.Scatter(x=normalized.index,y=normalized[sy],mode='lines',name=sy))
            fig.update_layout(height=430,yaxis_title='起點 = 100',margin=dict(l=5,r=5,t=20,b=5))
            st.plotly_chart(fig,use_container_width=True)
            st.caption(f'共同起始交易日：{normalized.index[0].date()}｜結束日：{normalized.index[-1].date()}｜收盤價標準化；未計入股息、匯率及交易成本；非交易日以前一筆收盤價對齊。')
            st.dataframe(pd.DataFrame({'期末指數':normalized.iloc[-1].round(2),'價格報酬率%':(normalized.iloc[-1]-100).round(2)}),use_container_width=True)
st.divider()
st.caption('資料來源：Yahoo Finance / Google News RSS。資料僅供研究參考，非投資建議；台灣上市與上櫃股票會依 .TW / .TWO 自動辨識。')
