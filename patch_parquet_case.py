"""
Idempotenter Patch: load_parquet() auf Title-Case Spalten (Open/High/Low/Close/Volume)
umstellen, damit die resample()-agg-Map in data_loader.py matcht.

Verwendung:
    python3 patch_parquet_case.py /pfad/zu/forex_bot_smc
"""
from __future__ import annotations
import io
import os
import re
import sys
from pathlib import Path


MARKER_ALREADY = 'title_map = {"open": "Open"'


def patch_data_loader(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")

    if MARKER_ALREADY in src:
        print(f"[{path.name}] schon gepatcht - skip.")
        return False

    # Ersetze die Zeile mit 'keep = [c for c in ("open","high","low","close","volume")'
    # durch eine Variante mit vorheriger Title-Case Rename-Map.
    old_block_re = re.compile(
        r'(\n[ \t]*)keep\s*=\s*\[c for c in \("open","high","low","close","volume"\) if c in df\.columns\][ \t]*\n'
        r'([ \t]*)df\s*=\s*df\[keep\][ \t]*\n',
        re.MULTILINE,
    )

    def _replace(m: re.Match) -> str:
        indent = m.group(1).lstrip("\n").rstrip(" \t")
        base = m.group(1)
        return (
            f'{base}title_map = {{"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}}\n'
            f'{indent}df = df.rename(columns=title_map)\n'
            f'{indent}keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]\n'
            f'{indent}df = df[keep]\n'
        )

    new_src, n = old_block_re.subn(_replace, src, count=1)
    if n == 0:
        print(f"[{path.name}] WARN: keep-Block nicht gefunden.")
        return False

    path.write_text(new_src, encoding="utf-8")
    print(f"[{path.name}] OK gepatcht ({n} Block ersetzt).")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 patch_parquet_case.py /pfad/zu/forex_bot_smc")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"Kein Verzeichnis: {root}")
        return 2

    dl = root / "data_loader.py"
    if not dl.is_file():
        print(f"Nicht gefunden: {dl}")
        return 2

    changed = patch_data_loader(dl)
    print("--- Fertig ---" if changed else "--- Keine Aenderung noetig ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
