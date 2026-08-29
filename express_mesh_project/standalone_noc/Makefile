CXX ?= g++
CXXFLAGS ?= -O3 -DNDEBUG -std=c++17 -Wall -Wextra -Wpedantic

.PHONY: all clean test

all: express_noc

express_noc: express_noc.cpp
	$(CXX) $(CXXFLAGS) $< -o $@

test: express_noc
	./express_noc --self-test

clean:
	rm -f express_noc
