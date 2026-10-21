#!/bin/bash
# Запуск дашборда: не даёт макбуку засыпать, пока работает сервер.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt || echo "Не все зависимости установились — см. ошибку выше"
exec caffeinate -dis .venv/bin/python server.py
