"""
backtest_intraday.py

Run an intraday EMA(3)/EMA(10)/EMA(50)/RSI(14) strategy on minute CSV data.

Usage examples:
  python3 backtest_intraday.py --folder Csvs/stock_data_aug_2025 --max-files 1 --out trade_log.csv

Inputs:
  - folder: directory containing daily CSVs with columns: ticker,time,open,high,low,close,volume

Outputs:
  - trade log CSV with one row per executed trade
  - performance metrics printed to stdout

This single-file script is deliberately self-contained and easy to run.
"""

import argparse
import glob
import math
import os
from datetime import timedelta

try:
    import numpy as np
    import pandas as pd
except Exception as e:
    raise SystemExit("Missing required packages: please pip install pandas numpy. Error: %s" % e)


BASE_CAPITAL = 1_000_000  # 10 lac
RISK_PER_TRADE_PCT = 0.005  # 0.5% of allocated capital


def read_day_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=['time'])
    # ensure columns are lowercase
    df.columns = [c.lower() for c in df.columns]
    df = df.sort_values(['ticker', 'time'])
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # note: use explicit 'min'/'T' is deprecated in newer pandas; caller should pass '10min' or '60min'
    o = df['open'].resample(rule).first()
    h = df['high'].resample(rule).max()
    l = df['low'].resample(rule).min()
    c = df['close'].resample(rule).last()
    v = df['volume'].resample(rule).sum()
    res = pd.concat([o, h, l, c, v], axis=1)
    res.columns = ['open', 'high', 'low', 'close', 'volume']
    res = res.dropna()
    return res


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(com=period-1, adjust=False).mean()
    ma_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ma_up / ma_down
    rsi = 100 - (100 / (1 + rs))
    return rsi


def select_top_by_turnover(df_min: pd.DataFrame, top_n: int = 10) -> list:
    # Compute turnover for first 10 minutes (09:15 - 09:24 inclusive)
    df = df_min.copy()
    df = df.set_index('time') if 'time' in df.columns else df
    first10 = df.between_time('09:15', '09:24')
    # turnover per ticker
    t = (first10['volume'] * first10['close']).groupby(first10['ticker']).sum()
    top = t.sort_values(ascending=False).head(top_n).index.tolist()
    return top


def compute_indicators(df_min: pd.DataFrame) -> (pd.DataFrame, pd.DataFrame):
    # df_min expected with datetime index
    df = df_min.copy()
    if 'time' in df.columns:
        df = df.set_index('time')

    # use explicit minute strings
    df_10 = resample_ohlcv(df, '10min')
    df_60 = resample_ohlcv(df, '60min')

    df_10['ema3'] = ema(df_10['close'], span=3)
    df_10['ema10'] = ema(df_10['close'], span=10)
    df_10['rsi14'] = rsi(df_10['close'], period=14)

    df_60['ema50'] = ema(df_60['close'], span=50)
    df_60 = df_60[['ema50']]
    # forward fill the 60-min ema50 to 10-min index
    df_10 = df_10.join(df_60.reindex(df_10.index, method='ffill'))
    return df_10, df


def size_from_risk(allocation: float, entry_price: float, stop_loss_pct: float) -> int:
    risk_amount = allocation * RISK_PER_TRADE_PCT
    qty = math.floor(risk_amount / (entry_price * stop_loss_pct))
    return max(qty, 0)


def simulate_for_ticker(ticker: str, df_min_all: pd.DataFrame, allocation: float):
    # df_min_all is all tickers minute data for the day; filter for ticker
    df_min = df_min_all[df_min_all['ticker'] == ticker].set_index('time')
    if df_min.empty:
        return []

    df_10, df_min_idx = compute_indicators(df_min.reset_index())

    trades = []

    for ts, row in df_10.iterrows():
        # skip if any NaNs
        if any(pd.isna([row['ema3'], row['ema10'], row['rsi14'], row['ema50']])):
            continue

        # Entry window is the 10-min candle represented by ts (09:15 covers 09:15..09:24)
        entry_time = ts + pd.Timedelta(minutes=9)  # use the last minute timestamp of the 10-min block

        # extract the minute bars for this 10-min block
        block = df_min_idx.loc[ts: entry_time]
        if block.empty:
            continue

        # Long entry
        if (row['ema3'] > row['ema10']) and (row['rsi14'] > 60) and (row['close'] > row['ema50']):
            entry_price = row['high']  # buy at high of entry 10-min candle
            stop_loss_price = entry_price * (1 - 0.005)
            target_price = entry_price * (1 + 0.02)
            qty = size_from_risk(allocation, entry_price, 0.005)
            if qty <= 0:
                continue

            # now simulate forward on 1-min bars after entry_time
            stop = stop_loss_price
            target = target_price
            trail_enabled = False
            highest = entry_price
            exited = False
            exit_price = None
            exit_time = None

            for t2, b in df_min_idx.loc[entry_time + timedelta(minutes=1):].iterrows():
                high = b['high']
                low = b['low']
                # update highest
                if high > highest:
                    highest = high
                # enable trailing if price >= entry * 1.005
                if (not trail_enabled) and (high >= entry_price * (1 + 0.005)):
                    trail_enabled = True
                    # set initial trailing stop
                    stop = max(stop, highest * (1 - 0.0075))
                # update trailing stop
                if trail_enabled:
                    stop = max(stop, highest * (1 - 0.0075))

                # check target first
                if high >= target:
                    exit_price = target
                    exit_time = t2
                    exited = True
                    break
                # check stop
                if low <= stop:
                    # exited at stop price
                    exit_price = stop
                    exit_time = t2
                    exited = True
                    break

            if not exited:
                # exit at close of day (last available bar)
                last_row = df_min_idx.iloc[-1]
                exit_price = last_row['close']
                exit_time = df_min_idx.index[-1]

            pnl = (exit_price - entry_price) * qty
            trades.append({
                'date': ts.date(),
                'ticker': ticker,
                'side': 'LONG',
                'entry_time': entry_time,
                'entry_price': entry_price,
                'qty': qty,
                'stop_loss': stop_loss_price,
                'target': target_price,
                'exit_time': exit_time,
                'exit_price': exit_price,
                'pnl': pnl,
            })

        # Short entry
        if (row['ema3'] < row['ema10']) and (row['rsi14'] < 30) and (row['close'] < row['ema50']):
            # entry price is the low of last 5 1-min candles within the 10-min block
            last5_end = entry_time
            last5_start = last5_end - timedelta(minutes=4)
            last5 = df_min_idx.loc[last5_start: last5_end]
            if last5.empty:
                continue
            entry_price = last5['low'].min()
            stop_loss_price = entry_price * (1 + 0.005)
            target_price = entry_price * (1 - 0.02)
            qty = size_from_risk(allocation, entry_price, 0.005)
            if qty <= 0:
                continue

            stop = stop_loss_price
            target = target_price
            trail_enabled = False
            lowest = entry_price
            exited = False
            exit_price = None
            exit_time = None

            for t2, b in df_min_idx.loc[entry_time + timedelta(minutes=1):].iterrows():
                high = b['high']
                low = b['low']
                if low < lowest:
                    lowest = low
                # enable trailing if price <= entry * (1 - 0.005)
                if (not trail_enabled) and (low <= entry_price * (1 - 0.005)):
                    trail_enabled = True
                    stop = min(stop, lowest * (1 + 0.0075))
                if trail_enabled:
                    stop = min(stop, lowest * (1 + 0.0075))

                # check target first (short target is lower)
                if low <= target:
                    exit_price = target
                    exit_time = t2
                    exited = True
                    break
                # check stop (for shorts hit if high >= stop)
                if high >= stop:
                    exit_price = stop
                    exit_time = t2
                    exited = True
                    break

            if not exited:
                last_row = df_min_idx.iloc[-1]
                exit_price = last_row['close']
                exit_time = df_min_idx.index[-1]

            pnl = (entry_price - exit_price) * qty
            trades.append({
                'date': ts.date(),
                'ticker': ticker,
                'side': 'SHORT',
                'entry_time': entry_time,
                'entry_price': entry_price,
                'qty': qty,
                'stop_loss': stop_loss_price,
                'target': target_price,
                'exit_time': exit_time,
                'exit_price': exit_price,
                'pnl': pnl,
            })

    return trades


def process_day_file(path: str, top_n: int = 10):
    print(f"Processing: {os.path.basename(path)}")
    df = read_day_csv(path)
    # get list of unique tickers
    tickers = df['ticker'].unique().tolist()

    # select top N by turnover at 09:25 (first 10 minutes 09:15-09:24)
    top = select_top_by_turnover(df.copy(), top_n=top_n)
    print(f"Selected top {len(top)} tickers")

    allocation = BASE_CAPITAL / max(len(top), 1)
    all_trades = []
    for t in top:
        trades = simulate_for_ticker(t, df, allocation)
        all_trades.extend(trades)

    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values('entry_time')
    return trades_df


def performance_from_trades(trades_df: pd.DataFrame):
    if trades_df.empty:
        return {'return': 0.0, 'max_drawdown': 0.0, 'win_rate': 0.0, 'sharpe': 0.0}

    # aggregate pnl per day
    trades_df['date'] = pd.to_datetime(trades_df['date'])
    daily = trades_df.groupby(trades_df['date'].dt.date)['pnl'].sum()
    total_return = daily.sum() / BASE_CAPITAL

    # equity series
    equity = (daily.cumsum() + BASE_CAPITAL).values
    peak = np.maximum.accumulate(equity)
    drawdown = (peak - equity) / peak
    max_dd = float(np.max(drawdown)) if len(drawdown) else 0.0

    wins = (trades_df['pnl'] > 0).sum()
    total = len(trades_df)
    win_rate = float(wins) / total if total else 0.0

    # Sharpe: use daily returns
    daily_rets = daily / BASE_CAPITAL
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = (daily_rets.mean() / daily_rets.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    return {
        'return': float(total_return),
        'max_drawdown': float(max_dd),
        'win_rate': float(win_rate),
        'sharpe': float(sharpe),
    }


def main(folder: str, max_files: int = 1, out: str = 'trade_log.csv'):
    files = sorted(glob.glob(os.path.join(folder, '*.csv')))
    files = files[:max_files]
    all_trades = []
    for f in files:
        trades_df = process_day_file(f, top_n=10)
        if not trades_df.empty:
            trades_df['source_file'] = os.path.basename(f)
            all_trades.append(trades_df)

    if all_trades:
        out_df = pd.concat(all_trades, ignore_index=True)
    else:
        out_df = pd.DataFrame()

    out_df.to_csv(out, index=False)
    metrics = performance_from_trades(out_df)
    print('\nPerformance metrics:')
    for k, v in metrics.items():
        print(f"{k}: {v}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', default='Csvs/stock_data_aug_2025', help='folder with day CSVs')
    parser.add_argument('--max-files', default=1, type=int, help='how many day files to process')
    parser.add_argument('--out', default='trade_log.csv', help='trade log csv output')
    args = parser.parse_args()
    main(args.folder, args.max_files, args.out)
