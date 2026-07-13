#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Standalone Tailwind CLI -- no Node.js/npm required, which keeps this a
# pure-Python build on Render. Cached between builds so it's only fetched once.
TAILWIND_VERSION="v3.4.19"
TAILWIND_BIN="./.tailwindcss-linux-x64"
TAILWIND_MIN_SIZE=1000000  # the real binary is several MB; anything smaller means a failed/partial download
if [ ! -s "$TAILWIND_BIN" ]; then
  # -f: fail loudly on a 4xx/5xx response instead of silently writing the
  # error page's body into $TAILWIND_BIN as if it were the binary.
  curl -fsSL -o "$TAILWIND_BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64"
  chmod +x "$TAILWIND_BIN"
fi
ACTUAL_SIZE=$(stat -c%s "$TAILWIND_BIN" 2>/dev/null || stat -f%z "$TAILWIND_BIN")
if [ "$ACTUAL_SIZE" -lt "$TAILWIND_MIN_SIZE" ]; then
  echo "ERROR: $TAILWIND_BIN is only $ACTUAL_SIZE bytes -- download is corrupt or incomplete." >&2
  rm -f "$TAILWIND_BIN"
  exit 1
fi
"$TAILWIND_BIN" -i ./static_src/input.css -o ./static/css/tailwind.css --minify

python manage.py collectstatic --no-input

python manage.py migrate

# Idempotent (get_or_create) -- safe to run on every deploy.
python manage.py seed_tracks
