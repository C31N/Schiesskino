#!/usr/bin/env bash
set -euo pipefail

SERVICE="laser-arcade.service"
LOGGER_TAG="schiesskino-start"

if systemctl is-active --quiet "${SERVICE}"; then
  exit 0
fi

if ! sudo -n /usr/bin/systemctl start "${SERVICE}"; then
  logger -t "${LOGGER_TAG}" "Dienst ${SERVICE} konnte nicht gestartet werden"
  exit 1
fi

# Ein defekter Start soll nicht so wirken, als hätte das Symbol gar nicht
# reagiert. Der Fehler landet nachvollziehbar im Systemprotokoll.
for _ in {1..20}; do
  systemctl is-active --quiet "${SERVICE}" && exit 0
  sleep 0.25
done
logger -t "${LOGGER_TAG}" "Dienst ${SERVICE} wurde nicht aktiv"
exit 1
