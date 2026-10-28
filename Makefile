# Local stack. Postgres and Redis live in Docker. The manual miner stays on
# the Windows host because the MetaTrader 5 API cannot run inside the alpine
# containers, and it must not share a process with bot_mt5.py.
# Live bot: rotation 5 modules / 4/5. Prefer start_all.ps1 over make for the full stack.

COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: up down miner stack

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

# Foreground worker. Ctrl-C stops it. The bot keeps trading either way.
miner:
	py manual_miner_daemon.py

# Data plane plus the miner in a new console. Optional; `make up` does not start it.
stack: up
	py -c "import subprocess, sys; subprocess.Popen([sys.executable, 'manual_miner_daemon.py'], creationflags=0x00000010)"
