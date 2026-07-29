import FinanceDataReader as fdr
import pandas as pd
import os

def update_market_list(market):
    backup_file = f'{market}_list_backup_v2.csv'
    try:
        # 한국거래소(KRX)에서 최신 종목 긁어오기
        df = fdr.StockListing(market)

        if df is not None and not df.empty:
            if 'Market' not in df.columns:
                df['Market'] = market 
            df = df[['Code', 'Name', 'Market']]
            df.to_csv(backup_file, index=False)
            print(f"✅ {market} 종목 리스트 업데이트 성공!")
    except Exception as e:
        print(f"❌ {market} 업데이트 실패 (IP 차단 등): {e}")

if __name__ == "__main__":
    update_market_list('KRX')
    update_market_list('KOSPI')
    update_market_list('KOSDAQ')
