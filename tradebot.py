import json
import time
import requests
import datetime
import telegram
import traceback

import pyupbit


# 거래 대금 상위 n개
def get_top_k(n):
    coin_list = []
    tickers = pyupbit.get_tickers(fiat="KRW")
    market_code = ','.join(tickers)

    url = "https://api.upbit.com/v1/ticker"
    params = {
        "markets": market_code
    }
    response = requests.get(url, params=params).json()

    for info in response:
        coin_list.append([info['market'], info['acc_trade_price_24h']])

    coin_list.sort(key=lambda x: x[1], reverse=True)

    top_k_list = []
    for i in coin_list:
        top_k_list.append(i[0])
    return top_k_list[:n]


# 당일 최고가 계산 (프로그램 시작시 이미 목표가 돌파한 코인 구매 방지)
def get_highest_price(tickers):
    highest_price = dict()
    market_code = ','.join(tickers)
    url = "https://api.upbit.com/v1/ticker"
    params = {
        "markets": market_code
    }
    response = requests.get(url, params=params).json()

    for info in response:
        highest_price[info['market']] = info['high_price']

    return highest_price


# 목표가 계산
def get_target_price_list(tickers, k):
    target_price_list = dict()
    for ticker in tickers:
        target_price_list[ticker] = get_target_price(ticker, k)
        time.sleep(0.1)  # 요청 수 제한 (초당 10회)

    return target_price_list


def get_target_price(ticker, k):
    yesterday = datetime.date.today() - datetime.timedelta(1)
    df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
    chart_yesterday = df.index[0].date()
    if chart_yesterday != yesterday:
        target_price = None
    else:
        target_price = df.iloc[0]['close'] + (df.iloc[0]['high'] - df.iloc[0]['low']) * k

    return target_price


# 이동평균선 계산
def get_ma(ticker, days):
    df = pyupbit.get_ohlcv(ticker, interval="day", count=days+1)
    ma = df['close'].rolling(window=days).mean()
    return ma[-2]


def get_ma_list(tickers, days):
    ma_list = dict()
    for ticker in tickers:
        ma_list[ticker] = get_ma(ticker, days)
        time.sleep(0.1)

    return ma_list


# Load API keys
with open("setting.json") as f:
    setting_loaded = json.loads(f.read())

# Upbit
access = setting_loaded["access_key"]
secret = setting_loaded["secret_key"]

# Telegram
telegram_token = setting_loaded["telegram_token"]
telegram_chat_id = setting_loaded["telegram_chat_id"]
telegram_bot = telegram.Bot(token=telegram_token)

# Initialize
upbit = pyupbit.Upbit(access, secret)
start_time = datetime.time(9)
sell_time = datetime.time(8, 59, 30)
tickrate = 0.5
fee = 0.0005
top_k = 20
portfolio_limit = 4
k_value = 0.5
stop_loss = 0.05
buymode = True  # ToDo: 매수 on off 추가


def telegram_send(message):
    telegram_bot.sendMessage(chat_id=telegram_chat_id, text='[UPbit 자동매매]\n'+message)


# # Load saved data
# try:
#     with open("data.json") as f:
#         data_loaded = json.loads(f.read())
#
#     updated_at = datetime.datetime.strptime(data_loaded['updated_at'], '%Y-%m-%d %H:%M:%S')
#
# except:
#     first_time = True
#
# if first_time:
#     updated_at = datetime.datetime.now()
#     top_k_list = get_top_k(top_k)
#     highest_price = get_highest_price(top_k_list)
#     ma_list = get_ma_list(top_k_list, 5)
#     balance = upbit.get_balance(ticker="KRW")
#     hold = []

updated_at = datetime.datetime.now()
top_k_list = get_top_k(top_k)
target_price_list = get_target_price_list(top_k_list, k_value)
highest_price = get_highest_price(top_k_list)
ma_list = get_ma_list(top_k_list, 5)
balance = upbit.get_balance(ticker="KRW")
hold = []
highest_price = get_highest_price(top_k_list)
high_price_track = highest_price
buy_list = []
buy_price = dict()
first_day = True

telegram_send('📢 프로그램 시작')

while True:
    try:
        now = datetime.datetime.now()
        current_price_list = pyupbit.get_current_price(top_k_list)

        if sell_time.hour == now.hour and sell_time.minute == now.minute and sell_time.second <= now.second < sell_time.second + 30:
            while hold:
                ticker = hold.pop()
                amount = upbit.get_balance(ticker)
                response = upbit.sell_market_order(ticker, amount)
                income = (current_price_list[ticker] - buy_price[ticker]) * amount
                telegram_send(f'⌛ 종가 매도\n📊 종목: {ticker}\n매도가: {current_price_list[ticker]}\n매수가: {buy_price[ticker]}\n수익: {income}\n{response}\n')
            time.sleep(30)
            continue

        # 9시 이전 판매, 9시 이후 새 타겟 프라이스 갱신 구현
        if start_time.hour == now.hour and start_time.minute + 1 == now.minute and start_time.second <= now.second < start_time.second + 10:
            updated_at = now
            top_k_list = get_top_k(top_k)
            target_price_list = get_target_price_list(top_k_list, k_value)
            ma_list = get_ma_list(top_k_list, 5)
            prev_balance = balance
            balance = upbit.get_balance(ticker="KRW")
            buy_list = []
            buy_price = dict()
            first_day = False
            telegram_send(f'⏳ 9시 재설정\n전일 잔고: {prev_balance}\n금일 잔고: {balance}\n수익: {balance - prev_balance}')
            time.sleep(10)
            continue

        for ticker in top_k_list:
            # 목표가 없을시 갱신하고 갱신해도 없을시 continue
            if target_price_list[ticker] is None:
                target_price_list[ticker] = get_target_price(ticker, k_value)
                if target_price_list[ticker] is None:
                    continue

            if ticker in buy_list:
                if ticker in hold:

                    # 최고가 계산
                    high_price_track[ticker] = max(high_price_track[ticker], current_price_list[ticker])

                    # 손절 계산
                    if current_price_list[ticker] < high_price_track[ticker]*(1-stop_loss):
                        amount = upbit.get_balance(ticker)
                        response = upbit.sell_market_order(ticker, amount)
                        hold.remove(ticker)
                        income = (current_price_list[ticker] - buy_price[ticker]) * amount
                        telegram_send(f'📉 손절\n종목: {ticker} / 매도가: {current_price_list[ticker]}\n매수가: {buy_price[ticker]}\n수익: {income}\n{response}')

                continue  # 샀으면 skip

            # 프로그램 가동 첫 날이면 고가와 목표가 비교 (이미 갱신했을 경우 구매 방지)
            if first_day and highest_price[ticker] > target_price_list[ticker]:
                continue

            # 목표가 돌파 및 5일 이평선 기준 상승장일시 매수
            if current_price_list[ticker] > target_price_list[ticker] and current_price_list[ticker] > ma_list[ticker] and len(buy_list) < portfolio_limit and buymode:
                response = upbit.buy_market_order(ticker, balance / portfolio_limit * (1-fee))
                high_price_track[ticker] = current_price_list[ticker]
                hold.append(ticker)
                buy_price[ticker] = current_price_list[ticker]
                buy_list.append(ticker)
                telegram_send(f'🛒 목표가 매수\n종목: {ticker}\n매수가: {current_price_list[ticker]}\n목표가: {target_price_list[ticker]}\n{response}')

        # # 정보 저장
        # data_container = dict()
        # data_container['updated_at'] = updated_at.strftime('%Y-%m-%d %H:%M:%S')
        # data_container['top_k_list'] = top_k_list
        # data_container['ma_list'] = ma_list
        # data_container['balance'] = balance
        # data_container['hold'] = hold
        #
        # with open("data.json", 'w') as f:
        #     json.dump(data_container, f, indent='\t')

    except Exception as e:
        telegram_send(f'🚨 에러 발생\n{traceback.format_exc()}')

    time.sleep(tickrate)
