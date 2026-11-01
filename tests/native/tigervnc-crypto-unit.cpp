// Keep assertions active when linked to the real Release viewer libraries.
#undef NDEBUG
#include <config.h>
#ifndef HAVE_NETTLE
#error "The viewer crypto regression requires enabled Nettle security support"
#endif
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
#include <nettle/sha1.h>
#include <nettle/version.h>
#include <rdr/AESInStream.h>
#include <rdr/AESOutStream.h>
#include <rdr/MemInStream.h>
#include <rdr/MemOutStream.h>

static_assert(SHA1_DIGEST_SIZE == 20 && EAX_DIGEST_SIZE == 16);
using ViewerCryptoBytes = std::vector<uint8_t>;
using ViewerCryptoKey = std::array<uint8_t, 32>;

// Trailing space catches a full digest accidentally written to an 8/16-byte region.
template<size_t Size> struct GuardedViewerDigest {
  std::array<uint8_t, Size + 2 * SHA1_DIGEST_SIZE> storage;
  GuardedViewerDigest() { storage.fill(0xa5); }
  uint8_t *data() { return storage.data() + SHA1_DIGEST_SIZE; }
  void check() const {
    for (size_t i = 0; i < storage.size(); ++i)
      if (i < SHA1_DIGEST_SIZE || i >= SHA1_DIGEST_SIZE + Size)
        assert(storage[i] == 0xa5);
  }
};

static const uint8_t sha1abc[SHA1_DIGEST_SIZE] = {
  0xa9,0x99,0x3e,0x36,0x47,0x06,0x81,0x6a,0xba,0x3e,
  0x25,0x71,0x78,0x50,0xc2,0x6c,0x9c,0xd0,0xd8,0x9d};

template<size_t Prefix> void digestBoundary() {
  GuardedViewerDigest<SHA1_DIGEST_SIZE> full;
  GuardedViewerDigest<Prefix> consumer;
  sha1_ctx context;
  const uint8_t abc[] = {'a','b','c'};
  sha1_init(&context);
  sha1_update(&context, sizeof(abc), abc);
#if NETTLE_VERSION_MAJOR >= 4
  sha1_digest(&context, full.data());
  std::copy_n(full.data(), Prefix, consumer.data());
#else
  sha1_digest(&context, SHA1_DIGEST_SIZE, full.data());
  sha1_init(&context);
  sha1_update(&context, sizeof(abc), abc);
  sha1_digest(&context, Prefix, consumer.data());
#endif
  full.check();
  consumer.check();
  assert(std::memcmp(full.data(), sha1abc, SHA1_DIGEST_SIZE) == 0);
  assert(std::memcmp(consumer.data(), sha1abc, Prefix) == 0);
  std::printf("SHA1 full 20 and retained prefix %zu: known vector/canaries PASS\n", static_cast<size_t>(Prefix));
}

ViewerCryptoBytes encrypt(const ViewerCryptoBytes &plain, const ViewerCryptoKey &key, int bits, size_t chunk, bool flushEach = false) {
  rdr::MemOutStream wire(1);
  rdr::AESOutStream cipher(&wire, key.data(), bits);
  for (size_t offset = 0; offset < plain.size();) {
    size_t count = std::min(chunk, plain.size() - offset);
    cipher.writeBytes(plain.data() + offset, count);
    if (flushEach) cipher.flush();
    offset += count;
  }
  cipher.flush();
  return {wire.data(), wire.data() + wire.length()};
}

void roundtrip(const ViewerCryptoKey &key, int bits, size_t length, size_t chunk, bool flushEach = false) {
  ViewerCryptoBytes plain(length);
  for (size_t i = 0; i < length; ++i) plain[i] = uint8_t(i * 131 + 17);
  ViewerCryptoBytes wire = encrypt(plain, key, bits, chunk, flushEach);
  size_t total = 0;
  for (size_t offset = 0; offset < wire.size();) {
    assert(wire.size() - offset >= 2 + EAX_DIGEST_SIZE);
    size_t payload = (size_t(wire[offset]) << 8) | wire[offset + 1];
    assert(payload > 0 && payload <= 8192);
    offset += 2 + payload + EAX_DIGEST_SIZE;
    assert(offset <= wire.size());
    total += payload;
  }
  assert(total == length);
  rdr::MemInStream input(wire.empty() ? key.data() : wire.data(), wire.size());
  rdr::AESInStream clear(&input, key.data(), bits);
  ViewerCryptoBytes restored(length);
  for (size_t offset = 0; offset < length;) {
    size_t count = std::min(size_t(509), length - offset);
    assert(clear.hasData(count));
    clear.readBytes(restored.data() + offset, count);
    offset += count;
  }
  assert(restored == plain && clear.pos() == length && input.pos() == wire.size());
}

void reject(const ViewerCryptoBytes &wire, const ViewerCryptoKey &key, int bits, const char *message = "authenticate") {
  rdr::MemInStream input(wire.data(), wire.size());
  rdr::AESInStream clear(&input, key.data(), bits);
  bool rejected = false;
  try { (void)clear.hasData(1); }
  catch (const std::runtime_error &error) {
    rejected = std::string(error.what()).find(message) != std::string::npos;
  }
  assert(rejected && clear.avail() == 0);
}

int main() {
  digestBoundary<8>();   // The displayed RealVNC fingerprint uses the first 8 bytes.
  digestBoundary<16>();  // AES128 derives its key from the first 16 SHA1 bytes.
  ViewerCryptoKey key;
  for (size_t i = 0; i < key.size(); ++i) key[i] = uint8_t(i * 7 + 3);
  for (int bits : {128, 256}) {
    for (size_t length : {0, 1, 15, 16, 17, 8191, 8192, 8193, 20000})
      for (size_t chunk : {1, 257, 65536}) roundtrip(key, bits, length, chunk);
    roundtrip(key, bits, 257, 1, true); // Cross the nonce counter's first-byte carry.
    ViewerCryptoBytes wire = encrypt(ViewerCryptoBytes(8193, 0x31), key, bits, 65536);
    size_t firstRecord = 2 + (size_t(wire[0]) << 8) + wire[1] + EAX_DIGEST_SIZE;
    for (size_t offset : {size_t(1), size_t(2), firstRecord - 1}) {
      ViewerCryptoBytes tampered = wire;
      tampered[offset] ^= 1;
      reject(tampered, key, bits);
    }
    ViewerCryptoKey wrong = key;
    wrong[0] ^= 1;
    reject(wire, wrong, bits);
    ViewerCryptoBytes reordered = wire;
    std::rotate(reordered.begin(), reordered.begin() + firstRecord, reordered.end());
    reject(reordered, key, bits);
    wire.resize(firstRecord - 1);
    reject(wire, key, bits, "End of stream");
    std::printf("AES%d real streams: lengths/chunks/nonce carry and all tamper rejections PASS\n", bits);
  }
  std::printf("PASS: viewer crypto regression on Nettle %d.%d; full RSA/UI handshake not exercised\n", NETTLE_VERSION_MAJOR, NETTLE_VERSION_MINOR);
}
