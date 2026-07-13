#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Standalone Tailwind CLI -- no Node.js/npm required, which keeps this a
# pure-Python build on Render. Cached between builds so it's only fetched once.
TAILWIND_VERSION="v3.4.19"
TAILWIND_BIN="./.tailwindcss-linux-x64"
if [ ! -f "$TAILWIND_BIN" ]; then
  curl -sSL -o "$TAILWIND_BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64"
  chmod +x "$TAILWIND_BIN"
fi
"$TAILWIND_BIN" -i ./static_src/input.css -o ./static/css/tailwind.css --minify

python manage.py collectstatic --no-input

python manage.py migrate
