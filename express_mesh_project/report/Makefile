LATEXMK ?= latexmk
FIGURES := $(wildcard ../results/20260831/standalone_suite/figures/*.svg)

.PHONY: all clean

all: main.pdf

main.pdf: main.tex $(FIGURES)
	$(LATEXMK) -pdf -shell-escape -interaction=nonstopmode -halt-on-error main.tex

clean:
	$(LATEXMK) -C main.tex
	rm -rf svg-inkscape
