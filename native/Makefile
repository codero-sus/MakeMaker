# mknative -- the C companion to MakeMaker
#
# make            build ../build/mknative
# make run        build and print the toolchain report
# make test       build and exercise the binary against a generated project
# make clean      remove build output

VERSION  ?= 0.1.0
CC       ?= cc
CFLAGS   += -std=c11 -O2 -Wall -Wextra -pedantic
CPPFLAGS += -DMAKERNATIVE_VERSION='"$(VERSION)"'

OUTDIR   := ../build
TARGET   := $(OUTDIR)/mknative

.PHONY: all run clean

all: $(TARGET)

$(TARGET): mknative.c mknative.h
	@mkdir -p $(OUTDIR)
	$(CC) $(CFLAGS) $(CPPFLAGS) mknative.c -o $(TARGET)

run: $(TARGET)
	$(TARGET) doctor

clean:
	rm -f $(TARGET)
