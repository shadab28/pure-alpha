import csv
from pathlib import Path
from typing import Dict, Optional


class InstrumentResolver:
    """Resolve trading symbol -> instrument_token using Csvs/instruments.csv.

    This is a lightweight local resolver that looks up the CSV and caches
    the mapping in memory for quick repeated resolution.
    """

    def __init__(self, csv_path: str = "Csvs/instruments.csv"):
        self.csv_path = Path(csv_path)
        self._map = None

    def _load(self):
        if not self.csv_path.exists():
            self._map = {}
            return
        m = {}
        with open(self.csv_path, newline='', encoding='utf-8') as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                sym = r.get('tradingsymbol')
                tok = r.get('instrument_token')
                if not sym or not tok:
                    continue
                try:
                    m[sym.upper()] = int(tok)
                except Exception:
                    continue
        self._map = m

    def resolve(self, symbol: str) -> Optional[int]:
        if self._map is None:
            self._load()
        return self._map.get(symbol.upper())

    def bulk_resolve(self, symbols):
        if self._map is None:
            self._load()
        out = {}
        for s in symbols:
            out[s] = self._map.get(s.upper())
        return out


def resolver_for_csv(csv_path="Csvs/instruments.csv"):
    return InstrumentResolver(csv_path)
