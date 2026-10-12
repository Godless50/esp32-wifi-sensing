// Host-only HMAC-SHA256 adapter for wsn_proto tests (OpenSSL).
#pragma once
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <stddef.h>
#include <stdint.h>

static inline void wsn_hmac_openssl(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t len,
                                    uint8_t out[32])
{
    unsigned int out_len = 32;
    HMAC(EVP_sha256(), key, (int)key_len, msg, len, out, &out_len);
}
