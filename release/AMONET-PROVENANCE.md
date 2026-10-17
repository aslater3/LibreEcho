# Community amonet/Kaeru packages served by dl.libreecho.org

LibreEcho does not ship these packages inside any release asset. From 0.13.21
the boot-chain repair, and the installers before it, download them from the
LibreEcho mirror at `https://dl.libreecho.org/amonet/<zip>`, served from R2.
The repair and the installers accept a package only when its size and SHA-256
match the pins below.

| Package | Size (bytes) | SHA-256 | Board |
| --- | --- | --- | --- |
| `amonet-radar-v1.0.0.zip` | 58531162 | `ecdb07bc05a508532e5ffed77121592d492b1a91572839e0f17545421f398f1a` | Echo (2nd Gen), `radar_puffin` |
| `amonet-biscuit-v2.0.0.zip` | 55989416 | `98297293701082bc7272efe077f941c56fc7b6e1f27ef6f2e93b6e4c6fc7b62d` | Echo Dot (2nd Gen), `biscuit` |

## Origin

The packages are the community releases by k4y0z and R0rt1z2, attached to
their XDA threads:

- [Echo 2nd Gen (radar)](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-2nd-gen-2017-radar.4801290/)
- [Echo Dot 2nd Gen (biscuit)](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/)

The mirrored bytes are identical to those releases.

Source code:

- [R0rt1z2/amonet, mt8163-radar branch](https://github.com/R0rt1z2/amonet/tree/mt8163-radar)
- [TWRP device tree](https://github.com/R0rt1z2/twrp_device_amazon_echo-mt8163)
- [amazon-oss/android_bootable_recovery](https://github.com/amazon-oss/android_bootable_recovery)

## Licence finding

- **amonet code:** the exploit and loader code in the packages is published
  under MIT and GPL-2.0, and the packages include those licence files.
- **Amazon binaries:** the bundled Amazon bootloader images (`preloader.img`,
  `lk.bin`, `tz.img`) are unmodified vendor binaries. No firmware-specific
  licence was found for them.
- **TWRP:** distributed under the TWRP and Android licences of its sources.

## Redistribution basis

Mirroring these packages is a documented good-faith decision by the maintainer
(2026-10-17), following established community precedent. The packages are
distributed openly by their authors for use on these devices, and the mirror
only makes the identical bytes reachable to the installers and the on-device
repair. LibreEcho never modifies them.
