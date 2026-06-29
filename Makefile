.PHONY: help dev test build seed serve up down logs clean

help:
	@echo "dev    - create local venv with engines + backend (editable)"
	@echo "test   - run the backend test suite"
	@echo "build  - build the dashboard"
	@echo "seed   - seed a demo dataset (uses ACP_CONFIG, e.g. config.local.json)"
	@echo "serve  - run the API locally (uses ACP_CONFIG)"
	@echo "up     - build and start the full Docker stack"
	@echo "down   - stop the Docker stack"

dev:
	bash scripts/dev-setup.sh

test:
	cd backend && ../.venv/bin/python -m pytest -q

build:
	cd frontend && npm install && npm run build

seed:
	.venv/bin/python -m app.cli seed

serve:
	.venv/bin/python -m app.cli serve

up:
	bash scripts/up.sh

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	rm -rf backend/data backend/engines_vendor frontend/dist
