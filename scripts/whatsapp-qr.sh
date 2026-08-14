#!/usr/bin/env bash
# Re-authenticate WhatsApp with a QR that actually fits the terminal.
#
# `npm run auth` prints the QR through qrcode-terminal, which draws TWO characters
# per module. A WhatsApp QR is version 11 (61 modules), so that needs ~130 columns
# and gets truncated in a normal 80-column terminal — the code is unreadable and
# unscannable. This renders the same payload with half-blocks instead: one column
# per module, two module-rows per text row, i.e. 65x33 for the same code.
#
# It also re-renders on every rotation (WhatsApp issues a fresh QR every ~20-30 s),
# so a code that expires while you reach for your phone is simply replaced.
set -uo pipefail
cd "$(dirname "$0")/.."

AUTH_DIR=store/auth
QR_FILE=store/qr-data.txt
STATUS_FILE=store/auth-status.txt

# A revoked session still has `registered: true` on disk, and the auth script exits
# with "Already authenticated" on that flag — so a re-auth after a `device_removed`
# logout silently does nothing until the old state is out of the way. Move it aside
# rather than delete it: if the pairing fails, the previous state is still there.
if [ -f "$AUTH_DIR/creds.json" ] && grep -q '"registered":true' "$AUTH_DIR/creds.json" 2>/dev/null; then
  dead="$AUTH_DIR.dead-$(date +%Y%m%d-%H%M%S)"
  mv "$AUTH_DIR" "$dead"
  echo "Existing session moved aside → $dead"
fi

rm -f "$QR_FILE" "$STATUS_FILE"
npm run auth >store/auth-run.log 2>&1 &
AUTH_PID=$!
trap 'kill "$AUTH_PID" 2>/dev/null' EXIT

render() {
  python3 - "$1" <<'PY'
import io, sys, qrcode
qr = qrcode.QRCode(border=2)
qr.add_data(open(sys.argv[1]).read().strip())
qr.make(fit=True)
buf = io.StringIO()
qr.print_ascii(out=buf, invert=True)
sys.stdout.write(buf.getvalue())
PY
}

echo "Waiting for a QR code…"
last=""
while kill -0 "$AUTH_PID" 2>/dev/null; do
  if [ -s "$QR_FILE" ]; then
    cur=$(cat "$QR_FILE")
    if [ "$cur" != "$last" ]; then
      last="$cur"
      clear
      render "$QR_FILE"
      echo "  WhatsApp → 설정 → 연결된 기기 → 기기 연결  (스캔하세요)"
      echo "  코드는 20-30초마다 갱신됩니다. 갱신되면 화면이 새로 그려집니다."
    fi
  fi
  if [ -s "$STATUS_FILE" ]; then
    status=$(cat "$STATUS_FILE")
    case "$status" in
      connected|authenticated|success) clear; echo "✓ 인증 완료"; break ;;
      failed:*) clear; echo "✗ 인증 실패: $status"; echo "  자세한 내용: store/auth-run.log"; break ;;
    esac
  fi
  sleep 1
done

wait "$AUTH_PID" 2>/dev/null
trap - EXIT
if [ -f "$AUTH_DIR/creds.json" ]; then
  echo
  echo "다음: systemctl --user start paperclaw"
else
  echo
  echo "세션이 만들어지지 않았습니다. store/auth-run.log 를 확인하세요."
fi
