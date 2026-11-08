.PHONY: setup seed run dashboard test eval demo

setup seed run dashboard test eval demo:
	uv run python tasks.py $@
