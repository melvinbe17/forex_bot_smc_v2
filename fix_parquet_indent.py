"""
Repariert die kaputten Zeilen in data_loader.py (Folgefehler aus patch_parquet_case.py).

Broken state nach dem fehlerhaften Patch:
    title_map = {"open": "Open", ...}          <- korrekt eingerueckt (4 Spaces)
df = df.rename(columns=title_map)                <- kaputt (Spalte 0)
keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]
df = df[keep]

Ziel: die 3 nackten Zeilen mit 4 Spaces Einrueckung versehen.
"""
from __future__ import annotations
from pathlib import Path
import sys


def patch(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")

    broken = (
        '    title_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}\n'
        'df = df.rename(columns=title_map)\n'
        'keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]\n'
        'df = df[keep]\n'
    )
    fixed = (
        '    title_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}\n'
        '    df = df.rename(columns=title_map)\n'
        '    keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]\n'
        '    df = df[keep]\n'
    )

    if broken in src:
        path.write_text(src.replace(broken, fixed), encoding="utf-8")
        print(f"[{path.name}] OK: Einrueckung repariert.")
        return True

    # Fallback: einzelne Zeilen pruefen + reparieren
    changed = False
    lines = src.splitlines(keepends=True)
    targets = {
        "df = df.rename(columns=title_map)\n": "    df = df.rename(columns=title_map)\n",
        'keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]\n':
            '    keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]\n',
        "df = df[keep]\n": "    df = df[keep]\n",
    }
    new_lines = []
    for ln in lines:
        if ln in targets:
            new_lines.append(targets[ln])
            changed = True
        else:
            new_lines.append(ln)
    if changed:
        path.write_text("".join(new_lines), encoding="utf-8")
        print(f"[{path.name}] OK (fallback): einzelne Zeilen reindented.")
        return True

    print(f"[{path.name}] nichts zu tun (schon in Ordnung oder anderer Zustand).")
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 fix_parquet_indent.py /pfad/zu/forex_bot_smc")
        return 2
    root = Path(sys.argv[1]).resolve()
    dl = root / "data_loader.py"
    if not dl.is_file():
        print(f"Nicht gefunden: {dl}")
        return 2
    patch(dl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
