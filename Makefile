PY ?= python3

.PHONY: all test demo eval eval-llm report serve clean

all: test demo eval report

test:
	$(PY) -m pytest -q

demo:
	$(PY) scripts/demo.py

eval:
	$(PY) evals/run_evals.py --gate

eval-llm:
	$(PY) evals/run_evals.py --llm

report:
	$(PY) -m handoff_lab.report

serve:
	$(PY) -m handoff_lab.server --port 8080

clean:
	rm -rf .pytest_cache **/__pycache__
