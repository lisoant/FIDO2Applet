# Runtime requirements

This app requires Javacard 3.0.4. I really, really, really wanted to
support Javacard 3.0.1, which runs on cool products like the
Mclear/NFCRings.com OMNI Ring (seriously nice tech!).

Unfortunately, CTAP2.0 requires an EC diffie-helmann key exchange
in order to support PINs or the hmac-secret extension, and it uses
DH with a SHA256 hash. Javacard 3.0.1 supports only ECDH-SHA1.
Javacard 3.0.4 doesn't support ECDH-SHA256 either... but it provides
a "plain" variant that returns the raw DH output which you then
hash yourself - good enough for me.

So it's not possible to make this app work in a meaningful way on
Javacard 3.0.1 or earlier.

So let's discuss the full requirements on the authenticator side:

- Javacard Classic 3.0.4
- Approximately 2kB of total RAM, of which around 300 bytes will be reserved
- Support for AES256-CBC
- Support for ECDH-plain
- Support for SHA-256 hashing
- Support for EC with 256-bit keys
- Approximately 15k of storage by default (very tunable)
- Ideally, support for EC TRANSIENT_DESELECT keys, as otherwise you'll get flash usage every app selection

An example of a card I've tested working is the NXP J3H145, but many
others should work fine too.

# Platform-side requirements

On the computer side of things, you'll likely want `libfido2` compiled
with support for PC/SC, which is currently experimental, or `libnfc`. On
Arch this is not the default - out of the box `libfido2` only works with
USB HID tokens, which this is **not**.

Without one of those two options you will Have A Bad Day.

If you have them, you should see the card start showing up in the output
of `fido2-token -L`. You can see what gets sent to and from the card by
setting `FIDO_DEBUG=1` before running your command.