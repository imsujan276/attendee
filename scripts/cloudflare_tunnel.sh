#!/bin/sh
# Starts a Cloudflare quick tunnel to the local app, points EXTERNAL_WEBHOOK_SITE_DOMAIN in .env at it,
# restarts the containers that read .env and moves the calendar notification channels to the new
# address. Unlike free ngrok, it serves Chrome the app directly,
# which signed-in Google Meet bots need. The URL changes on every run, so the Google Workspace
# SSO profile has to be updated with the URLs printed below each time.
set -eu
cd "$(dirname "$0")/.."

command -v cloudflared >/dev/null || { echo "cloudflared not found. Install it with: brew install cloudflared"; exit 1; }

log=$(mktemp)
cloudflared tunnel --url http://localhost:8007 >"$log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; rm -f "$log"' EXIT INT TERM

host=""
for _ in $(seq 30); do
  host=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 | sed 's|https://||' || true)
  [ -n "$host" ] && break
  sleep 1
done
[ -n "$host" ] || { cat "$log"; echo "cloudflared did not print a tunnel URL"; exit 1; }

touch .env
if grep -q '^EXTERNAL_WEBHOOK_SITE_DOMAIN=' .env; then
  sed -i.bak "s|^EXTERNAL_WEBHOOK_SITE_DOMAIN=.*|EXTERNAL_WEBHOOK_SITE_DOMAIN=$host|" .env
else
  echo "EXTERNAL_WEBHOOK_SITE_DOMAIN=$host" >>.env
fi
if ! grep -q '^ALLOWED_HOSTS=' .env; then
  echo "ALLOWED_HOSTS=localhost,.trycloudflare.com" >>.env
elif ! grep -q '^ALLOWED_HOSTS=.*\.trycloudflare\.com' .env; then
  sed -i.bak 's|^ALLOWED_HOSTS=.*|&,.trycloudflare.com|' .env
fi
rm -f .env.bak

if docker compose -f dev.docker-compose.yaml restart attendee-app-local attendee-worker-local attendee-scheduler-local; then
  # Calendar notification channels keep the address they were created with, so Google would keep
  # notifying the old tunnel until they expire. Replace them now.
  docker compose -f dev.docker-compose.yaml exec -T attendee-worker-local python manage.py shell -c '
from bots.models import Calendar, CalendarNotificationChannel, CalendarStates
from bots.tasks.sync_calendar_task import enqueue_sync_calendar_task

CalendarNotificationChannel.objects.all().delete()
calendars = Calendar.objects.filter(state=CalendarStates.CONNECTED)
for calendar in calendars:
    enqueue_sync_calendar_task(calendar)
print(f"Re-syncing {len(calendars)} calendar(s) with the new address")
' || echo "Could not replace the calendar notification channels."
else
  echo "Could not restart the containers. Run make up once they're built."
fi

echo
echo "Tunnel:       https://$host"
echo "Sign-in URL:  https://$host/bot_sso/google_meet_sign_in"
echo "Sign-out URL: https://$host/bot_sso/google_meet_sign_out"
echo "Set these in the Google Workspace legacy SSO profile. Press Ctrl-C to stop the tunnel."

wait $pid
