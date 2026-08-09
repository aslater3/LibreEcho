# Core runtime source-offer status

This release record describes the core-image runtime closure. It is not a
license grant and it does not clear the aggregate component until every shipped
runtime input has a complete corresponding-source and relink path.

## Closed subcomponents in this candidate

- `wireless-tools 30~pre9` is rebuilt from the pinned upstream archive
  `https://archive.ubuntu.com/ubuntu/pool/main/w/wireless-tools/wireless-tools_30~pre9.orig.tar.gz`,
  SHA-256
  `abd9c5c98abf1fdd11892ac2f8a56737544fe101e1be27c6241a564948f34c63`.
  The Platform builder records the compiler, exported Linux UAPI identity,
  static ELF contract, binary identity, and complete upstream `COPYING` file.
- `wireless-regdb 2025.10.07` is materialized from the Ubuntu upstream archive
  `https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/wireless-regdb/2025.10.07-0ubuntu1~24.04.1/wireless-regdb_2025.10.07.orig.tar.xz`,
  SHA-256
  `d4c872a44154604c869f5851f7d21d818d492835d370af7f58de8847973801c3`.
  The materializer verifies that both `regulatory.db` and
  `regulatory.db.p7s` exactly match the bytes used by the image overlay before
  the Build pipeline proceeds.

These checks close provenance for those two inputs; they do not by themselves
clear the aggregate runtime component.

## Remaining aggregate blocker

The exact source archives, build records, and corresponding-source/relinkable
object offer for the remaining TinyALSA, libsodium, glibc, and GCC runtime
closure have not yet been assembled and independently verified for the exact
shipped outputs. The release gate must therefore continue to report
`core-runtime-closure` as `blocked`.
