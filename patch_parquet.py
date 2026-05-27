from pathlib import Path
import re

def patch_data_loader():
    p = Path("data_loader.py")
    src = p.read_text()
    if "def load_parquet" in src:
        print("[data_loader.py] schon gepatcht - skip.")
        return
    anchor = "def load_symbol(symbol: str,"
    if anchor not in src:
        raise SystemExit("ERROR: Anker def load_symbol fehlt")
    func = (
        'def load_parquet(symbol: str, timeframe: str,\n'
        '                 data_dir=None):\n'
        '    if data_dir is None:\n'
        '        data_dir = "data/dukascopy"\n'
        '    path = Path(data_dir) / f"{symbol}_{timeframe}_15y.parquet"\n'
        '    if not path.exists():\n'
        '        raise FileNotFoundError(f"Parquet nicht gefunden: {path}")\n'
        '    df = pd.read_parquet(path)\n'
        '    df.columns = [c.strip().lower() for c in df.columns]\n'
        '    if not isinstance(df.index, pd.DatetimeIndex):\n'
        '        for col in ("datetime", "timestamp", "time", "date"):\n'
        '            if col in df.columns:\n'
        '                df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert(None)\n'
        '                df = df.set_index(col)\n'
        '                break\n'
        '    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:\n'
        '        df.index = df.index.tz_convert(None)\n'
        '    df = df.sort_index()\n'
        '    keep = [c for c in ("open","high","low","close","volume") if c in df.columns]\n'
        '    df = df[keep]\n'
        '    df = df[~df.index.duplicated(keep="first")]\n'
        '    return df\n\n\n'
    )
    src = src.replace(anchor, func + anchor)
    old = '        if source in ("csv", "auto"):'
    if src.count(old) != 1:
        raise SystemExit("ERROR: dispatch-Anker nicht eindeutig")
    new = (
        '        if source == "parquet":\n'
        '            df = load_parquet(symbol, tf)\n'
        '        if df is None and source in ("csv", "auto"):'
    )
    src = src.replace(old, new)
    old_cli = 'choices=["auto", "csv", "yfinance"]'
    new_cli = 'choices=["auto", "csv", "parquet", "yfinance"]'
    if old_cli not in src:
        raise SystemExit("ERROR: CLI choices fehlt")
    src = src.replace(old_cli, new_cli)
    p.write_text(src)
    print("[data_loader.py] OK gepatcht.")

def patch_backtest():
    b = Path("backtest_m15.py")
    bsrc = b.read_text()
    pat = re.compile(r'choices=\[([^\]]*?"yfinance"[^\]]*?)\]')
    matches = list(pat.finditer(bsrc))
    changed = 0
    for m in reversed(matches):
        if '"parquet"' in m.group(1):
            continue
        new_c = m.group(1).rstrip() + ', "parquet"'
        bsrc = bsrc[:m.start(1)] + new_c + bsrc[m.end(1):]
        changed += 1
    if changed:
        b.write_text(bsrc)
        print("[backtest_m15.py] OK: " + str(changed) + " Liste(n) erweitert.")
    else:
        print("[backtest_m15.py] nichts zu tun (schon drin oder nicht gefunden).")

patch_data_loader()
patch_backtest()
print("Fertig.")
