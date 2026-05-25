#!/usr/bin/env python3
"""Extract MAX messenger (ru.oneme.app) PmsKey definitions from any version
of the APK, regardless of ProGuard/R8 obfuscation.

Stable anchor: every property accessor is registered in <clinit> as a pair
    const-string vA, "<kebab-key>"
    const-string vB, "<methodName>()L<obfuscated-pkg>/PmsProperty;"
followed by a Kotlin property-reference factory call. ProGuard renames the
enclosing class and the property-holder type, but the simple name
`PmsProperty` (an SDK interface) is preserved. We rely only on that.

Usage:
    python3 pms_extract.py path/to/app.apk           # decodes with apktool
    python3 pms_extract.py path/to/decoded_dir       # reuses apktool output
    python3 pms_extract.py app.apk -o out.json --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

CONST_STRING_RE = re.compile(r'const-string(?:/jumbo)?\s+\S+,\s*"((?:[^"\\]|\\.)*)"')
SIGNATURE_RE = re.compile(r'^[\w$\-]+\(\)L([\w/$]+)/PmsProperty;$')


def ensure_decoded(target: Path) -> tuple[Path, bool]:
    """Return (decoded_dir, is_temp). Decodes with apktool if given an .apk."""
    if target.is_dir() and any((target / sub).is_dir()
                               for sub in ("smali", "smali_classes2", "smali_classes3")):
        return target, False
    if target.is_file() and target.suffix.lower() == ".apk":
        if not shutil.which("apktool"):
            sys.exit("apktool not found in PATH; install it or pass an apktool-decoded directory")
        out = Path(tempfile.mkdtemp(prefix="pms_decode_"))
        print(f"decoding {target.name} -> {out}", file=sys.stderr)
        subprocess.run(["apktool", "d", "-f", "-q", "-o", str(out), str(target)], check=True)
        return out, True
    sys.exit(f"don't know how to read {target} (need .apk file or apktool-decoded dir)")


def iter_smali(root: Path):
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and sub.name.startswith("smali"):
            yield from sub.rglob("*.smali")


def extract_from_text(text: str) -> list[tuple[str, str, str]]:
    """Returns list of (key, method, pms_pkg) tuples in source order."""
    pairs = []
    prev_value: str | None = None
    for m in CONST_STRING_RE.finditer(text):
        value = m.group(1)
        sig_match = SIGNATURE_RE.match(value)
        if sig_match and prev_value is not None and "PmsProperty" not in prev_value:
            method = value.split("(", 1)[0]
            pms_pkg = sig_match.group(1)
            pairs.append((prev_value, method, pms_pkg))
        prev_value = value
    return pairs


def find_pms_class(root: Path):
    by_file: dict[Path, list[tuple[str, str, str]]] = {}
    pkg_votes: Counter[str] = Counter()
    for smali in iter_smali(root):
        try:
            text = smali.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "PmsProperty;" not in text:
            continue
        pairs = extract_from_text(text)
        if pairs:
            by_file[smali] = pairs
            for _k, _m, p in pairs:
                pkg_votes[p] += 1
    if not by_file:
        return None, [], None
    best_file, best_pairs = max(by_file.items(), key=lambda kv: len(kv[1]))
    best_pkg = pkg_votes.most_common(1)[0][0]
    return best_file, best_pairs, best_pkg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help=".apk file or apktool-decoded directory")
    ap.add_argument("-o", "--output", default="pmskeys.json", help="output JSON path (default: pmskeys.json)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    decoded, is_temp = ensure_decoded(Path(args.target).resolve())
    try:
        src, pairs, pms_pkg = find_pms_class(decoded)
        if not pairs:
            print("no PmsKey entries found", file=sys.stderr)
            return 2

        if args.verbose:
            print(f"PmsKey holder smali: {src.relative_to(decoded)}", file=sys.stderr)
            print(f"PmsProperty package: {pms_pkg.replace('/', '.')}", file=sys.stderr)
            print(f"entries: {len(pairs)}", file=sys.stderr)
            dupes = [k for k, n in Counter(k for k, *_ in pairs).items() if n > 1]
            if dupes:
                print(f"warning: duplicate keys: {dupes}", file=sys.stderr)

        result = {
            "class": f"{pms_pkg.replace('/', '.')}.PmsKey",
            "powered_by": "t.me/teamkomet",
            "total_keys": len(pairs),
            "keys": [k for k, _m, _p in pairs],
        }
        Path(args.output).write_text(json.dumps(result, indent=4, ensure_ascii=False))
        print(f"wrote {args.output} ({len(pairs)} keys)")
        return 0
    finally:
        if is_temp:
            shutil.rmtree(decoded, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
