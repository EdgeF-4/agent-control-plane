.PHONY: help dev test build seed serve up down logs smoke clean

help:
	@echo "dev    - create local venv with engines + backend (editable)"
	@echo "test   - run the backend test suite"
	@echo "build  - build the dashboard"
	@echo "seed   - seed a demo dataset (uses ACP_CONFIG, e.g. config.local.json)"
	@echo "serve  - run the API locally (uses ACP_CONFIG)"
	@echo "up     - build and start the full Docker stack in the background"
	@echo "smoke  - verify the running API and dashboard"
	@echo "down   - stop the Docker stack"

dev:
	bash scripts/dev-setup.sh

test:
	@test -x .venv/bin/python || { echo "Missing .venv. Next: run 'make dev', then rerun 'make test'." >&2; exit 2; }
	@cd backend && ../.venv/bin/python -m pytest -q || { status=$$?; echo "Backend tests failed with exit $$status. Fix the first reported test failure, then rerun 'make test'." >&2; exit $$status; }

build:
	@cd frontend && npm ci && npm run build || { status=$$?; echo "Dashboard build failed with exit $$status. Fix the first package or compiler error above, then rerun 'make build'." >&2; exit $$status; }

seed:
	@test -x .venv/bin/python || { echo "Missing .venv. Run 'make dev' first, then rerun 'make seed'." >&2; exit 2; }
	@.venv/bin/python -m app.cli seed || { status=$$?; echo "Seeding failed with exit $$status. Follow the preceding Next instruction, then rerun 'make seed'." >&2; exit $$status; }

serve:
	@test -x .venv/bin/python || { echo "Missing .venv. Run 'make dev' first, then rerun 'make serve'." >&2; exit 2; }
	@.venv/bin/python -m app.cli serve || { status=$$?; echo "Server startup failed with exit $$status. Follow the preceding Next instruction, then rerun 'make serve'." >&2; exit $$status; }

up:
	bash scripts/up.sh

smoke:
	bash scripts/smoke.sh

down:
	@docker compose down || { status=$$?; echo "Stack shutdown failed with exit $$status. Start the container daemon or correct the Compose error above, then rerun 'make down'." >&2; exit $$status; }

logs:
	@docker compose logs -f || { status=$$?; if [ $$status -ne 130 ]; then echo "Log streaming failed with exit $$status. Start the container daemon or correct the Compose error above, then rerun 'make logs'." >&2; fi; exit $$status; }

clean:
	@rm -rf backend/data backend/engines_vendor frontend/dist || { status=$$?; echo "Cleanup failed with exit $$status. Correct the reported ownership or permission error, then rerun 'make clean'." >&2; exit $$status; }
