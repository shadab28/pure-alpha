"""Run a sample insertion into ohlcv_data table."""
from datetime import datetime, UTC
from pgAdmin_database.ohlcv_utils import create_table, insert_ohlcv

def main():
    create_table()
    insert_ohlcv('INFY', '1d', 1510.0, 1525.0, 1490.5, 1518.7, 2500000, datetime.now(UTC))
    print('Sample row upsert complete')

if __name__ == '__main__':
    main()