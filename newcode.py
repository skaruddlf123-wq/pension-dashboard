# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
import pandas_ta as ta
import yfinance as yf  # <-- 야후 파이낸스 전용 공식 라이브러리 추가
from datetime import datetime, timedelta
import os

st.set_page_config(page_title="주간 트레이딩 대시보드", layout="wide")

st.title("📈 주간 시스템 트레이딩 조건검색 대시보드")
st.markdown("""
**검색 조건 (주봉 기준):**
1. **Stochastic Slow (20, 12, 12):** %K가 %D를 상향돌파
2. **MACD (5, 20, 9):** MACD가 Signal을 상향돌파
3. **RSI (30, 9):** RSI가 Signal을 상향돌파
""")

# 1. 백업 파일 생성 로직 (Market 컬럼 추가)
@st.cache_data(ttl=86400) 
def get_market_list(market):
    backup_file = f'{market}_list_backup_v2.csv'
    try:
        df = fdr.StockListing(market)
        if 'Market' not in df.columns:
            df['Market'] = market 
        
        df = df[['Code', 'Name', 'Market']]
        df.to_csv(backup_file, index=False)
        return df
    except Exception:
        if os.path.exists(backup_file):
            return pd.read_csv(backup_file)
        else:
            st.error(f"{market} 종목 데이터를 불러오지 못했습니다.")
            return pd.DataFrame(columns=['Code', 'Name', 'Market'])

# 2. 하이브리드 주가 데이터 수집 로직 (★ 네이버 1차 -> 야후 yfinance 2차 우회)
@st.cache_data(show_spinner=False)
def get_stock_data(ticker, name, market, include_current_week):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)
    
    df = pd.DataFrame()
    
    # [1차 시도] FinanceDataReader (네이버 금융) - 로컬 PC에서 제일 빠르고 정확함
    try:
        df = fdr.DataReader(ticker, start_date, end_date)
    except Exception:
        pass
        
    # [2차 시도] 네이버에서 차단당했거나 실패했을 경우 -> yfinance (야후 파이낸스)로 즉시 우회
    if df is None or df.empty:
        if market in ['KOSPI', 'KOSDAQ']:
            suffix = '.KS' if market == 'KOSPI' else '.KQ'
            yahoo_ticker = f"{ticker}{suffix}"
        else:
            yahoo_ticker = f"{ticker}.KS" if int(ticker) < 100000 else f"{ticker}.KQ"
            
        try:
            stock_obj = yf.Ticker(yahoo_ticker)
            df = stock_obj.history(start=start_date, end=end_date)
            # 야후 파이낸스의 타임존(Timezone) 정보를 제거하여 네이버 데이터와 형식 통일
            if not df.empty:
                df.index = df.index.tz_localize(None)
        except Exception:
            return None
            
    if df is None or df.empty:
        return None
    
    # 일봉 -> 주봉 리샘플링
    df_weekly = df.resample('W').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()

    if not include_current_week:
        df_weekly = df_weekly.iloc[:-1]

    # 지표 계산
    stoch = df_weekly.ta.stoch(k=20, d=12, smooth_k=12)
    if stoch is not None and not stoch.empty:
        df_weekly = pd.concat([df_weekly, stoch], axis=1)
        k_col, d_col = stoch.columns[0], stoch.columns[1] 
    else:
        return None
    
    macd = df_weekly.ta.macd(fast=5, slow=20, signal=9)
    if macd is not None and not macd.empty:
        df_weekly = pd.concat([df_weekly, macd], axis=1)
        macd_col, sig_col = macd.columns[0], macd.columns[2]
    else:
        return None

    df_weekly['RSI_30'] = df_weekly.ta.rsi(length=30)
    df_weekly['RSI_Signal_9'] = df_weekly['RSI_30'].rolling(window=9).mean()

    df_weekly = df_weekly.dropna()
    if df_weekly.empty:
        return None

    df_weekly['Prev_K'] = df_weekly[k_col].shift(1)
    df_weekly['Prev_D'] = df_weekly[d_col].shift(1)
    df_weekly['Prev_MACD'] = df_weekly[macd_col].shift(1)
    df_weekly['Prev_MACD_Sig'] = df_weekly[sig_col].shift(1)
    df_weekly['Prev_RSI'] = df_weekly['RSI_30'].shift(1)
    df_weekly['Prev_RSI_Sig'] = df_weekly['RSI_Signal_9'].shift(1)

    cond_stoch = (df_weekly['Prev_K'] < df_weekly['Prev_D']) & (df_weekly[k_col] > df_weekly[d_col])
    cond_macd = (df_weekly['Prev_MACD'] < df_weekly['Prev_MACD_Sig']) & (df_weekly[macd_col] > df_weekly[sig_col])
    cond_rsi = (df_weekly['Prev_RSI'] < df_weekly['Prev_RSI_Sig']) & (df_weekly['RSI_30'] > df_weekly['RSI_Signal_9'])

    df_weekly['Stoch_GC'] = cond_stoch
    df_weekly['MACD_GC'] = cond_macd
    df_weekly['RSI_GC'] = cond_rsi
    
    latest = df_weekly.iloc[-1]
    
    return {
        '종목명': name,
        '종목코드': ticker,
        '현재가': int(latest['Close']),
        '스토캐스틱_돌파': bool(latest['Stoch_GC']),
        'MACD_돌파': bool(latest['MACD_GC']),
        'RSI_돌파': bool(latest['RSI_GC']),
        '3조건_동시만족': bool(latest['Stoch_GC'] and latest['MACD_GC'] and latest['RSI_GC'])
    }

# UI 구성
with st.sidebar:
    st.header("⚙️ 검색 설정")
    market_option = st.selectbox(
        "검색 대상", 
        ["샘플 종목 (빠른 테스트)", "KOSPI 전체", "KOSDAQ 전체", "KRX 전체(코스피+코스닥)"]
    )
    max_items = st.number_input("최대 검색 종목 수 (0=제한 없음)", min_value=0, value=0, step=100)
    include_current_week = st.checkbox("진행 중인 이번 주 포함", value=False)
    st.write("---")
    run_button = st.button('🚀 주간 조건검색 실행', use_container_width=True)

if run_button:
    with st.spinner('시장 데이터를 불러오는 중입니다...'):
        if market_option == "샘플 종목 (빠른 테스트)":
            target_list = pd.DataFrame({
                'Code': ['005930', '000660', '035420', '035720', '005380'], 
                'Name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차'],
                'Market': ['KOSPI', 'KOSPI', 'KOSPI', 'KOSPI', 'KOSPI']
            })
        elif market_option == "KOSPI 전체":
            target_list = get_market_list('KOSPI')
        elif market_option == "KOSDAQ 전체":
            target_list = get_market_list('KOSDAQ')
        else:
            target_list = get_market_list('KRX')
        
        if max_items > 0:
            target_list = target_list.head(max_items)
            
        total_cnt = len(target_list)
        st.info(f"총 {total_cnt}개 종목 스캔을 시작합니다.")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        failed_tickers = []
        
        for i, row in target_list.iterrows():
            ticker = row['Code']
            name = row['Name']
            market = row.get('Market', 'KOSPI')
            
            status_text.text(f"스캔 중... ({i+1}/{total_cnt}) : {name}")
            progress_bar.progress((i + 1) / total_cnt)
            
            try:
                res = get_stock_data(ticker, name, market, include_current_week)
                if res:
                    results.append(res)
                else:
                    failed_tickers.append({'종목코드': ticker, '종목명': name, '사유': '데이터 부재(상장폐지 등)'})
            except Exception as e:
                failed_tickers.append({'종목코드': ticker, '종목명': name, '사유': '계산 중 치명적 에러'})
        
        status_text.text("스캔 완료!")
        
        st.subheader("📊 조건검색 결과")
        if results:
            result_df = pd.DataFrame(results)
            perfect_match = result_df[result_df['3조건_동시만족'] == True]
            
            if not perfect_match.empty:
                st.success(f"🎉 3가지 지표를 모두 만족하는 종목이 {len(perfect_match)}개 포착되었습니다.")
                st.dataframe(perfect_match, use_container_width=True)
            else:
                st.warning("이번 주에는 3가지 지표를 동시에 상향 돌파한 종목이 없습니다.")
            
            with st.expander(f"전체 검색 결과 보기 ({len(result_df)}개)"):
                st.dataframe(result_df, use_container_width=True)
                
            csv = result_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 성공 결과를 엑셀(CSV)로 다운로드",
                data=csv,
                file_name=f"weekly_trading_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv',
            )
        else:
            st.error("조건을 만족하는 종목이 하나도 없거나 주가 데이터를 전혀 불러오지 못했습니다.")

        if failed_tickers:
            with st.expander(f"⚠️ 데이터 검증: 스캔 실패/제외 종목 ({len(failed_tickers)}개) - 투자 전 반드시 확인"):
                st.dataframe(pd.DataFrame(failed_tickers), use_container_width=True)
