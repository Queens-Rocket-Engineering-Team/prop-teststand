#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <input-header> <output-expanded-header>" >&2
  exit 2
fi

IN_HEADER=$1
OUT_HEADER=$2

gcc -E "$IN_HEADER" | \
  awk 'BEGIN{in_our_header=0} /^# [0-9]+ /{marker=$3; gsub(/^"/, "", marker); gsub(/"$/, "", marker); if (marker ~ /^<.*>$/) {in_our_header=0; next} is_system=(marker ~ /^\/usr\// || marker ~ /^\/lib\// || marker ~ /^\/lib64\// || marker ~ /^\/include\// || marker ~ /^\/opt\//); in_our_header=!is_system; next} in_our_header{print}' \
  > "$OUT_HEADER"

if [ ! -s "$OUT_HEADER" ]; then
  echo "ERROR: expanded header is empty" >&2
  exit 1
fi

echo "Wrote expanded header to $OUT_HEADER"
