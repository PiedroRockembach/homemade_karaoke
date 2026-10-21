# Karaokê local — comandos. Tudo roda dentro de .venv/ (sem sudo).
VENV   ?= .venv
PY     := $(VENV)/bin/python
DENO   := $(VENV)/bin/deno
PORT   ?= 8765
WEB    := karaoke/web

.PHONY: install start dev test test-all lint fmt doctor update-ytdlp firewall firewall-undo clean help

help:
	@echo "make install | start | dev | test | test-all | lint | fmt | doctor | update-ytdlp | firewall | firewall-undo"

install:
	bash scripts/install.sh

start:
	@test -x $(PY) || { echo "A app ainda não foi instalada. Rode primeiro: make install"; exit 1; }
	$(PY) -m karaoke

dev:
	KARAOKE_NO_BROWSER=1 PATH="$(CURDIR)/$(VENV)/bin:$$PATH" \
	  $(PY) -m uvicorn karaoke.app:create_app --factory --reload --host 127.0.0.1 --port $(PORT)

test:
	$(PY) -m pytest -m "not gpu and not network and not e2e"

test-all:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(DENO) lint $(WEB)
	$(DENO) fmt --check $(WEB)

fmt:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
	$(DENO) fmt $(WEB)

doctor:
	$(PY) -m karaoke.doctor

update-ytdlp:
	$(PY) -m pip install -U "yt-dlp[default,deno]"

firewall:
	bash scripts/firewall-linux.sh --yes

firewall-undo:
	bash scripts/firewall-linux.sh --undo

clean:
	rm -rf .pytest_cache .ruff_cache build *.egg-info
