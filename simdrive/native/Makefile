BIN = ../src/simdrive/_bin/simdrive-input
SRC = src/simdrive_input.m

# SimulatorKit is not linked: its location differs across Xcode versions, so
# the helper dlopens it at runtime from the selected developer dir.
CORESIM_FW = /Library/Developer/PrivateFrameworks

# Universal2: single binary that runs on Apple Silicon AND Intel macs.
ARCHS = -arch arm64 -arch x86_64

CFLAGS = -fobjc-arc -O2 -Wall -Wno-deprecated-declarations -Isrc $(ARCHS)
LDFLAGS = -framework Foundation -framework CoreGraphics \
          -F$(CORESIM_FW) -framework CoreSimulator \
          -Wl,-rpath,$(CORESIM_FW)

all: $(BIN)

$(BIN): $(SRC)
	@mkdir -p $(dir $(BIN))
	xcrun clang $(CFLAGS) $(LDFLAGS) $(SRC) -o $(BIN)
	@file $(BIN)
	@echo "Built: $(BIN)"

# Single-arch fallback if universal build fails (e.g., on a host that can't cross-compile).
arm64-only: ARCHS = -arch arm64
arm64-only: $(BIN)

clean:
	rm -f $(BIN)
