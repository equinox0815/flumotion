include $(top_srcdir)/common/python.mk

flumotiondir = $(libdir)/flumotion/python/flumotion/monitor/checkmk

flumotion_PYTHON =	\
	__init__.py	\
	main.py

TAGS_FILES = $(flumotion_PYTHON)
ETAGS_ARGS = --lang=python $(flumotion_PYTHON)

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(flumotion_PYTHON)
