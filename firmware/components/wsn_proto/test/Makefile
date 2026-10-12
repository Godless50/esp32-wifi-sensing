CC ?= gcc
CFLAGS += -std=c11 -Wall -Wextra -Werror -O1 -I../include -I.
LDLIBS += -lcrypto
SRC := ../wsn_proto.c
UTIL := ../wsn_util.c
GOLDEN_OUT := ../../../../server/tests/golden/c_packets.hex

.PHONY: test golden clean

test: host_test util_test
	./host_test
	./util_test

golden: golden_bin
	mkdir -p $(dir $(GOLDEN_OUT))
	./golden_bin > $(GOLDEN_OUT)

host_test: host_test.c $(SRC) ../include/wsn_proto.h hmac_openssl.h
	$(CC) $(CFLAGS) -o $@ host_test.c $(SRC) $(LDLIBS)

util_test: util_test.c $(UTIL) ../include/wsn_util.h
	$(CC) $(CFLAGS) -o $@ util_test.c $(UTIL)

golden_bin: golden.c $(SRC) ../include/wsn_proto.h hmac_openssl.h
	$(CC) $(CFLAGS) -o $@ golden.c $(SRC) $(LDLIBS)

clean:
	rm -f host_test util_test golden_bin
