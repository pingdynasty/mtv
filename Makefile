.PHONY: test smoke check

test:
	python3 -m unittest discover -s tests -v

smoke:
	python3 tests/smoke_browser.py

check: test smoke
