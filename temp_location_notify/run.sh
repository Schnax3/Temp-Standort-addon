#!/usr/bin/with-contenv bashio

bashio::log.info "Starte Standort-Alarme ..."
exec python3 -u /app/main.py
