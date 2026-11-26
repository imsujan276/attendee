#!/bin/sh
# Puts the current code live: builds the image, runs migrations, restarts the host redis-server
# and recreates the containers. Also sets up a new server. If a step fails, the containers already
# running are left as they are. attendee-app collects static files itself when it starts.
set -eu
cd "$(dirname "$0")/.."

compose="docker compose -f prod.docker-compose.yaml"

$compose build
$compose run --rm --no-deps attendee-app python manage.py migrate
sudo systemctl restart redis-server
$compose up -d --force-recreate
