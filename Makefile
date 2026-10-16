PY ?= .venv/bin/python
.PHONY: setup test demo clean
setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
test:
	$(PY) -m pytest -q
demo:
	$(PY) -m capraiq demo --out out
clean:
	rm -rf out .pytest_cache
