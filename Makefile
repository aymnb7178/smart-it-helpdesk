.PHONY: install test demo serve clean

install:
	python -m pip install -r requirements.txt

test:
	python -m pytest -q

demo:
	python scripts/demo.py

serve:
	python -m helpdesk serve --db build/demo.db

clean:
	rm -rf build .pytest_cache models
