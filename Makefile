#!/usr/bin/make -f

watch:
	python certificate_agent.py

clean:
	find . -type f -name '*.pyc' -delete
