Intraday EMA-RSI Backtester

This repository contains `backtest_intraday.py`, a single-file intraday backtester implementing an EMA-RSI strategy.

Requirements
- Python 3.8+
- pandas
- numpy

Install requirements:

pip install -r requirements.txt

or

pip install pandas numpy

Usage

Run a smoke test on one CSV (default the CSVs are in `Csvs/stock_data_aug_2025`):

python3 backtest_intraday.py --folder Csvs/stock_data_aug_2025 --max-files 1 --out trade_log.csv

Outputs
- `trade_log.csv` (or the path you pass to --out): per-trade log with entry/exit and pnl
- Performance metrics printed to stdout (return, max_drawdown, win_rate, sharpe)

Notes
- CSVs are expected to have columns: `ticker,time,open,high,low,close,volume`
- Time should be parseable by pandas `to_datetime` (e.g., `2025-08-01 09:15:00`)
- The script uses fixed risk parameters matching the requested strategy; modify constants inside the script to change base capital or risk.
