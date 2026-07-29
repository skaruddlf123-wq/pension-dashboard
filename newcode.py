# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
import pandas_ta as ta
from datetime import datetime, timedelta
import os

# 페이지 기본 설정
st.set_page_config(page_title="주간 트레이딩 대시보드", layout="wide")

st.title("📈 주간 시스템 트레이딩 조건검색 대시보드")
st.markdown("""
**검색 조건 (주봉 기준):**
1. **Stochastic Slow (20, 12, 12):** %K가 %D를 상향돌파 (Golden Cross)
2. **MACD (5, 20, 9):** MACD가 Signal을 상향돌파 (Golden Cross)
3. **RSI (30, 9):** RSI가 Signal을 상향돌파 (Golden Cross)
""")

# --- 새로 추가된 핵심 로직: 종목 리스트 캐싱 및 CSV 파일 백업 시스템 ---
@st.cache_data(ttl=86400) 
def get_market_list(market):
    backup_file = f'{market}_list_backup.csv'
    try:
        # 1차 시도: 외부 서버에서 최신 데이터 긁어오기
        df = fdr.StockListing(market)[['Code', 'Name']]
        # 통신 성공 시, 혹시 모를 차단에 대비해 로컬에 몰래 최신 상태로 백업 저장
        df.to_csv(backup_file, index=False)
        return df
    except Exception:
        # 2차 시도: 에러(IP 차단 등) 발생 시 프로그램 뻗지 않고 기존 백업 파일 조용히 불러오기
        if os.path.exists(backup_file):
            return pd.read_csv(backup_file)
        else:
            st.error(f"{market} 종목 데이터를 최초로 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
            return pd.DataFrame(columns=['Code', 'Name'])
# --------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_stock_data(ticker, name, include_current_week):
    # 최근 2년치 데이터 불러오기 (주봉 계산용 여유 데이터)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)
    
    df = fdr.DataReader(ticker, start_date, end_date)
    if df.empty:
        return None
    
    # 일봉 데이터를 주봉(Weekly) 데이터로 리샘플링
    df_weekly = df.resample('W').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()

    if not include_current_week:
        # 이번 주 데이터(아직 금요일 장마감 전인 미완성 캔들)를 제외하여 리페인팅 방지
        df_weekly = df_weekly.iloc[:-1]

    # 1. Stochastic Slow (20, 12, 12) 계산
    stoch = df_weekly.ta.stoch(k=20, d=12, smooth_k=12)
    if stoch is not None and not stoch.empty:
        df_weekly = pd.concat([df_weekly, stoch], axis=1)
        k_col, d_col = stoch.columns[0], stoch.columns[1] 
    else:
        return None
    
    # 2. MACD (5, 20, 9) 계산
    macd = df_weekly.ta.macd(fast=5, slow=20, signal=9)
    if macd is not None and not macd.empty:
        df_weekly = pd.concat([df_weekly, macd], axis=1)
        macd_col, sig_col = macd.columns[0], macd.columns[2]
    else:
        return None

    # 3. RSI (30) 및 Signal (9) 계산
    df_weekly['RSI_30'] = df_weekly.ta.rsi(length=30)
    df_weekly['RSI_Signal_9'] = df_weekly['RSI_30'].rolling(window=9).mean()

    # 지표 계산에 필요한 초기 데이터 결측치(NaN) 제거
    df_weekly = df_weekly.dropna()
    if df_weekly.empty:
        return None

    # 최근 주(이번 주) 데이터와 직전 주(지난주) 데이터 비교를 위한 Shift 처리
    df_weekly['Prev_K'] = df_weekly[k_col].shift(1)
    df_weekly['Prev_D'] = df_weekly[d_col].shift(1)
    
    df_weekly['Prev_MACD'] = df_weekly[macd_col].shift(1)
    df_weekly['Prev_MACD_Sig'] = df_weekly[sig_col].shift(1)
    
    df_weekly['Prev_RSI'] = df_weekly['RSI_30'].shift(1)
    df_weekly['Prev_RSI_Sig'] = df_weekly['RSI_Signal_9'].shift(1)

    # 골든크로스 조건 확인 (직전엔 아래에 있다가, 이번에 위로 뚫고 올라옴)
    cond_stoch = (df_weekly['Prev_K'] < df_weekly['Prev_D']) & (df_weekly[k_col] > df_weekly[d_col])
    cond_macd = (df_weekly['Prev_MACD'] < df_weekly['Prev_MACD_Sig']) & (df_weekly[macd_col] > df_weekly[sig_col])
    cond_rsi = (df_weekly['Prev_RSI'] < df_weekly['Prev_RSI_Sig']) & (df_weekly['RSI_30'] > df_weekly['RSI_Signal_9'])

    df_weekly['Stoch_GC'] = cond_stoch
    df_weekly['MACD_GC'] = cond_macd
    df_weekly['RSI_GC'] = cond_rsi
    
    # 가장 최근 1주(마지막 행)의 결과만 추출
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

# 좌측 사이드바(Sidebar) UI 구성
with st.sidebar:
    st.header("⚙️ 검색 설정")
    market_option = st.selectbox(
        "검색 대상", 
        ["샘플 종목 (5개 빠른 테스트)", "KOSPI 전체", "KOSDAQ 전체", "KRX 전체(코스피+코스닥)"]
    )
    max_items = st.number_input("최대 검색 종목 수 (0=제한 없음)", min_value=0, value=0, step=100)
    include_current_week = st.checkbox(
        "진행 중인 이번 주 포함", 
        value=False, 
        help="체크 해제 시 완전히 마감된 지난주까지의 데이터로만 확실하게 판정합니다. (권장)"
    )
    st.write("---")
    run_button = st.button('🚀 주간 조건검색 실행', use_container_width=True)

if run_button:
    with st.spinner('시장 데이터를 불러오는 중입니다...'):
        # 1. 대상 종목 리스트 가져오기 (fdr.StockListing 직접 호출을 새로 만든 자동 우회 함수로 교체)
        if market_option == "샘플 종목 (5개 빠른 테스트)":
            target_list = pd.DataFrame({
                'Code': ['005930', '000660', '035420', '035720', '005380'], 
                'Name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차']
            })
        elif market_option == "KOSPI 전체":
            target_list = get_market_list('KOSPI')
        elif market_option == "KOSDAQ 전체":
            target_list = get_market_list('KOSDAQ')
        else:
            target_list = get_market_list('KRX')
        
        # 최대 검색 종목 수 제한 적용
        if max_items > 0:
            target_list = target_list.head(max_items)
            
        total_cnt = len(target_list)
        st.info(f"총 {total_cnt}개 종목 스캔을 시작합니다. 종목 수에 따라 시간이 걸릴 수 있습니다.")
        
        # UI 프로그레스 바 및 텍스트
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        failed_tickers = []
        
        # 2. 각 종목별로 분석 진행
        for i, row in target_list.iterrows():
            ticker = row['Code']
            name = row['Name']
            
            # 진행 상태 업데이트
            status_text.text(f"스캔 중... ({i+1}/{total_cnt}) : {name}")
            progress_bar.progress((i + 1) / total_cnt)
            
            try:
                res = get_stock_data(ticker, name, include_current_week)
                if res:
                    results.append(res)
                else:
                    failed_tickers.append({'종목코드': ticker, '종목명': name, '사유': '과거 데이터 부족 (신규상장 등)'})
            except Exception as e:
                # 에러 발생 시 건너뛰고 기록 (중단 방지)
                failed_tickers.append({'종목코드': ticker, '종목명': name, '사유': '계산 에러/거래정지'})
        
        status_text.text("스캔 완료!")
        
        # 3. 결과 출력
        if results:
            result_df = pd.DataFrame(results)
            
            st.subheader("📊 조건검색 결과")
            # 3가지 지표를 모두 만족하는 알짜 종목 필터링
            perfect_match = result_df[result_df['3조건_동시만족'] == True]
            
            if not perfect_match.empty:
                st.success(f"🎉 축하합니다! 3가지 지표를 모두 만족하는 종목이 {len(perfect_match)}개 포착되었습니다.")
                st.dataframe(perfect_match, use_container_width=True)
            else:
                st.warning("이번 주에는 3가지 지표(Stoch, MACD, RSI)를 동시에 상향 돌파한 종목이 없습니다.")
            
            with st.expander(f"전체 검색 결과 보기 ({len(result_df)}개)"):
                st.dataframe(result_df, use_container_width=True)
                
            # 실패한 종목 내역 출력
            if failed_tickers:
                with st.expander(f"스캔 실패/제외 종목 ({len(failed_tickers)}개) - 데이터 부족 등"):
                    st.dataframe(pd.DataFrame(failed_tickers), use_container_width=True)
                
            # 엑셀(CSV) 다운로드 기능 (utf-8-sig 처리를 통해 엑셀에서도 한글 안 깨짐)
            csv = result_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 성공 결과를 엑셀(CSV)로 다운로드",
                data=csv,
                file_name=f"weekly_trading_screen_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv',
            )
        else:
            st.error("데이터를 불러올 수 없습니다. 네트워크 상태를 확인해 주세요.")
