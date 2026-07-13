# Polaris AI — dev convenience targets. The canonical bring-up is `docker compose up`.
.PHONY: up down build logs ps test migrate makemigrations shell psql spike-test fmt seed seed-reset sync-prompts

start up:            ## Bring the whole stack up (build if needed)
	docker compose up --build

start-d up-d:          ## Bring the stack up detached
	docker compose up --build -d

stop down:          ## Stop and remove containers
	docker compose down

stop-v down-v:        ## Stop and remove containers + volumes (fresh clone equivalent)
	docker compose down -v

build:
	docker compose build

logs:
	docker compose logs -f

ps:
	docker compose ps

# --- backend (run inside the backend container) ---
test:          ## Run the P0 spike test suite in-container (the gate out of P0)
	docker compose exec backend sh -c "python manage.py makemigrations --noinput && pytest -q"

seed:          ## Seed the King County demo data (idempotent)
	docker compose exec backend python manage.py seed_kc

seed-reset:    ## Rebuild the seed from scratch (fresh dates)
	docker compose exec backend python manage.py seed_kc --reset

sync-prompts:  ## Push code prompts to Langfuse (create-if-missing; --update/--promote via ARGS)
	docker compose exec backend python manage.py sync_prompts $(ARGS)

migrate:
	docker compose exec backend python manage.py migrate

makemigrations:
	docker compose exec backend python manage.py makemigrations

shell:
	docker compose exec backend python manage.py shell

psql:
	docker compose exec postgres psql -U polaris -d polaris

fmt:           ## Format + lint backend
	docker compose exec backend sh -c "black . && ruff check --fix ."
