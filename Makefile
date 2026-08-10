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
	@test -x .venv/bin/python || { echo "Missing .venv. Run 'make dev' first." >&2; exit 2; }
	cd backend && ../.venv/bin/python -m pytest -q

build:
	cd frontend && npm ci && npm run build

seed:
	.venv/bin/python -m app.cli seed

serve:
	.venv/bin/python -m app.cli serve

up:
	bash scripts/up.sh

smoke:
	bash scripts/smoke.sh

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	rm -rf backend/data backend/engines_vendor frontend/dist
