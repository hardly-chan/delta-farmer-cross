.PHONY: lint update clean deploy stats stats-was stats-now
p ?= all

lint:
	uv run ruff format .
	uv run ruff check --fix .
	uv run pyright

test:
	uv run pytest -v

update:
	uv sync --upgrade --all-groups

clean:
	rm -rf .ruff_cache .venv uv.lock .python-version
	find . -type f -name "*.pyc" -delete

stats:
	@echo "\n── pacifica ──" && uv run -m apps.pacifica stats $(p)
	@echo "\n── ethereal ──" && uv run -m apps.ethereal stats $(p)
	@echo "\n── omni ──" && uv run -m apps.omni stats $(p)
	@echo "\n── nado ──" && uv run -m apps.nado stats $(p)

stats-was:
	@$(MAKE) -s stats p=last

stats-now:
	@$(MAKE) -s stats p=this

# --- Deploy ---

HOST=lab
EXEC=ssh -tt $(HOST)
SYNC=rsync -avz --delete-after --exclude={'.git','.venv','.*cache','__pycache__','.DS_Store','*.pyc','.env'}
DDIR=~/delta-farmer
UV=~/.local/bin/uv

deploy:
	$(SYNC) ./ $(HOST):$(DDIR)
	$(EXEC) "cd $(DDIR) && $(UV) sync"
