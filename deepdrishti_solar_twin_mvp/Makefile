.PHONY: install run test reset

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	. .venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	. .venv/bin/activate && pytest -q

reset:
	curl -X POST http://127.0.0.1:8000/api/reset
