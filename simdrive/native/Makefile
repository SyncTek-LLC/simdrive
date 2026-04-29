BIN = ../src/simdrive/_bin/simdrive-input
SRC = src/simdrive_input.m

XCODE_DEV = /Applications/Xcode.app/Contents/Developer
SIMKIT_FW = $(XCODE_DEV)/Library/PrivateFrameworks
CORESIM_FW = /Library/Developer/PrivateFrameworks

CFLAGS = -fobjc-arc -O2 -Wall -Wno-deprecated-declarations -Isrc
LDFLAGS = -framework Foundation -framework CoreGraphics \
          -F$(SIMKIT_FW) -framework SimulatorKit \
          -F$(CORESIM_FW) -framework CoreSimulator \
          -Wl,-rpath,$(SIMKIT_FW) \
          -Wl,-rpath,$(CORESIM_FW)

all: $(BIN)

$(BIN): $(SRC)
	@mkdir -p $(dir $(BIN))
	xcrun clang $(CFLAGS) $(LDFLAGS) $(SRC) -o $(BIN)
	@echo "Built: $(BIN)"

clean:
	rm -f $(BIN)
