.PHONY: test smoke check build clean

test:
	python3 -m unittest discover -s tests -t . -v

smoke:
	python3 tests/smoke_browser.py

check: test smoke

build:
	python3 -m pip wheel --no-deps -w dist .

clean:
	rm -rf dist build *.egg-info
