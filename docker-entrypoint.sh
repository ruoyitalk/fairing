#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

secret_dir="${FAIRING_SECRET_DIR:-/run/homeserver-secrets}"
runtime_dir="${FAIRING_AUTH_RUNTIME_DIR:-/run/fairing-auth}"
redirect_uri="${FAIRING_OIDC_REDIRECT_URI:-https://ruoyi.net.cn/oauth2callback}"
home_dir="${HOME:-/tmp/fairing-home}"

for name in google_client_id google_client_secret context_session_secret; do
  test -s "$secret_dir/$name" || {
    echo "missing required Fairing auth secret: $name" >&2
    exit 1
  }
done

install -d -m 0700 "$home_dir"
install -d -m 0700 "$runtime_dir"
umask 077
{
  printf '[auth]\n'
  printf 'redirect_uri = "%s"\n' "$redirect_uri"
  printf 'cookie_secret = "%s"\n' "$(cat "$secret_dir/context_session_secret")"
  printf '\n[auth.google]\n'
  printf 'client_id = "%s"\n' "$(cat "$secret_dir/google_client_id")"
  printf 'client_secret = "%s"\n' "$(cat "$secret_dir/google_client_secret")"
  printf 'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n'
} > "$runtime_dir/secrets.toml"

exec streamlit run streamlit_app.py --secrets.files="$runtime_dir/secrets.toml" "$@"
