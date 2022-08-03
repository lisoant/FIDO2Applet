# Frequently Asked Questions

## How can you have an FAQ on a newly-created repository? Surely the questions aren't frequent yet.

You caught me, I'm a fraud and these are anticipatory questions.

## What's FIDO2?

If you don't know what that is, you don't need this.

## What's a Javacard?

If you don't know what that is, you DEFINITELY don't need this.

## Don't you need a CBOR parser to write a CTAP2 authenticator?

Apparently not. Instead of implementing a real CBOR parser I just
poured more sweat into the implementation, and added a topping of
non-standards-compliance.

As a result of not having a proper CBOR parser, the app will often
return undesirable error codes on invalid input, but it should
handle most valid input acceptably.

It does this by linearly scanning a byte-buffer with the CBOR object in it,
and moving a read index forward the desired amount. Unknown objects get skipped.
Any object declaring a length greater than two bytes long causes an error,
because it's not possible to have >65535 of something in a 1,024-byte-long
buffer, and the CTAP2 standard requires that CBOR be "canonical".

## Why did you write this, when someone else said they were almost done writing a better version?

Well, they said that, but they hadn't published the source code and I got impatient.

Two is better than zero, right?

## Why did you write this at all?

I was pretty unhappy with the idea of trusting my "two factor" SSH
keys' security to a closed-source hardware device, and even the
existing open hardware devices didn't work the way I wanted.

I wanted my password to be used in such a way that without it, the
authenticator was useless - in other words, a true second factor.

So I wrote a CTAP2 implementation that [had that property](security.md).

## You say there are "caveats" for some implementation bits. What are those?

Well, first off, this app doesn't attempt to do a full CBOR parse, so its error
statuses often aren't perfect and it's generally tolerant of invalid input.

Secondly, OpenSSH has a bug that rejects makeCredential responses
that don't have credProtect level two when it requests level two. The
CTAP2.1 standard says it's okay to return level three if two was requested,
but that breaks OpenSSH, so... credProtect is incorrectly implemented in
that it always applies level three internally.

Finally, the CTAP API requires user presence detection, but there's really no
way to do that on Javacard 3.0.4. We can't even use the "presence timeout"
that is described in the spec for NFC devices. So you're always treated as
being present, which is to some extent offset by the fact that anything real
requires you type your PIN (if one is set)...

So set a PIN, and unplug your card when you're not using it.

## Why don't you implement U2F/CTAP1?

U2F doesn't support PINs, and requires an attestation certificate.

[The security model](security.md) requires PINs.

It would be possible to implement U2F commands in non-standards-compliant ways,
but implementing them the normal way would require turning off the `alwaysUv`
key feature for U2F-accessible credentials.

## Isn't PBKDF2 on a smartcard a fig leaf?

Probably, yes, but it makes me feel better.

You can raise the iteration count, but really there's only so much that can be
done here. At least it means off-the-shelf rainbow tables probably won't work.

## I hear bcrypt or Argon2id is better than PBKDF2

Good luck implementing those on a 16-bit microprocessor. I welcome you to try.

## What does this implementation store for resident keys?

It will store:
- the credential ID (an AES256 encrypted blob of the RP ID SHA-256
  hash and the credential private key)
- up to 32 characters of the RP ID, again AES256 encrypted
- a 64-character-long user ID, again AES256 encrypted
- the length of the RP ID, unencrypted
- the length of the user ID, unencrypted
- a boolean set to true on the first credential from a given RP ID, used
  to save state when enumerating and counting on-device RPs
- how many distinct RPs have valid keys on the device, unencrypted
- how many total RPs are on the device, unencrypted

This is the minimum to make the credentials management API work. It would
be possible to encrypt the length fields too, they just aren't and I didn't
see it as important.

The default is to have fifty slots for resident keys, which is double what a
Yubikey supports. You can turn this up, with a performance and flash cost, or
turn it down with a performance and flash benefit.

## Why is the code quality so low?

You're welcome to contribute to improving it. I wrote this for a purpose and
it seems to work for that purpose.

Please remember that this code is written for processors that don't have an
`int` type - only `short`. Most function calls are a runtime overhead, and
each object allocation comes out of your at-most-2kB of RAM available. You
can't practically use dynamic memory allocation at all, it's just there to tease
you.

The code I wrote may look ugly, and it's certainly not perfect, but it is
reasonably efficient in execution on in-order processors with very limited
stacks.

## Why is the smartcard giving me OPERATION_DENIED when I try to create a resident key?

You haven't set a PIN. You can turn off this feature in the code, or you can
set a PIN. If I were you, I would use a PIN with resident keys.

## I'm getting some strange CBOR error when I try to use this

Run the app in JCardSim with VSmartCard and hook up your Java debugger.
See what's going on. Raise a pull request to fix it.