#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(realpath "${1:?usage: bash patches/patch_apk.sh original.apk output.apk}")
OUTPUT=$(realpath -m "${2:?output.apk required}")
: "${APKTOOL_JAR:?Set APKTOOL_JAR to the Apktool jar}"
[[ "$SOURCE" != "$OUTPUT" && ! -e "$OUTPUT" ]] || { echo "Output must be a new file" >&2; exit 1; }
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT
if [[ -n "${FINGERPRINT_PATCH:-}" ]]; then
  (umask 077; printf '%s' "$FINGERPRINT_PATCH" > "$WORK/fingerprint.py")
elif [[ -n "${FINGERPRINT_PATCH_FILE:-}" && -f "$FINGERPRINT_PATCH_FILE" ]]; then
  (umask 077; cp -- "$FINGERPRINT_PATCH_FILE" "$WORK/fingerprint.py")
else
  echo "FINGERPRINT_PATCH secret or FINGERPRINT_PATCH_FILE is required" >&2
  exit 1
fi
unset FINGERPRINT_PATCH
java -jar "$APKTOOL_JAR" d -f -o "$WORK/decoded" "$SOURCE"
if ! python3 "$WORK/fingerprint.py" "$WORK/decoded" "$SOURCE" > "$WORK/fingerprint.log" 2>&1; then
  echo "Fingerprint patch failed; private output suppressed" >&2
  exit 1
fi
apk-mitm --apktool "$APKTOOL_JAR" --tmp-dir "$WORK/mitm" "$WORK/decoded"
[[ -s "$WORK/decoded-patched.apk" ]] || { echo "apk-mitm produced no APK" >&2; exit 1; }
apksigner verify --verbose "$WORK/decoded-patched.apk"
zipalign -c -p 4 "$WORK/decoded-patched.apk"
mv -- "$WORK/decoded-patched.apk" "$OUTPUT"
