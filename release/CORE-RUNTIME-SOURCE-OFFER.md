# Core runtime source-offer status

This release record describes the core-image runtime closure. It is not a
license grant and it does not clear the aggregate component until every shipped
runtime input has a complete corresponding-source and relink path.

## Closed subcomponents in this candidate

- `libnl 3.11.0` is rebuilt from the pinned upstream archive
  `https://github.com/thom311/libnl/releases/download/libnl3_11_0/libnl-3.11.0.tar.gz`,
  SHA-256
  `2a56e1edefa3e68a7c00879496736fdbf62fc94ed3232c0baba127ecfa76874d`.
  The public Platform builder creates static `libnl-3` and `libnl-genl-3`
  archives and links them into wpa_supplicant; the complete application and
  library source plus build instructions provide the relink path.
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
- `libsodium 1.0.18` is rebuilt from the pinned upstream archive
  `https://archive.ubuntu.com/ubuntu/pool/main/libs/libsodium/libsodium_1.0.18.orig.tar.gz`,
  SHA-256
  `d59323c6b712a1519a5daf710b68f5e7fde57040845ffec53850911f10a5d4f4`.
  The OTA verifier links against that generated static archive and its staged
  headers, not the AirPlay sysroot archive.
- `TinyALSA e43025bbf702eb7dd8edd48c1eb50530c60f1de8` is rebuilt with the
  checked-in MT8163 patch from its pinned BSD-3-Clause archive. The builder
  verifies the archive and patch hashes, static ARM32 outputs, and every
  shipped utility hash; BSD-3-Clause creates no static relinkable-object
  obligation.

These checks close provenance for these five inputs. The aggregate runtime
closure is independently bound to the exact candidate source-offer and relink
object records named in `release/components.json`.

## Aggregate closure

The exact glibc and GCC runtime source archives, build records, corresponding
source, and relinkable-object offer are represented by the pinned
`core-runtime-closure` source-offer identity. The release gate must fail closed
if that identity or its independent member-hash/relink verification changes.
