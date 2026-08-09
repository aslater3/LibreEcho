# MT8163 audio FPGA provenance and release decision

This is a WHENCE-style provenance record for the required audio bridge
`i2s_to_spi_v34.bin`. It documents a deliberate good-faith redistribution
 decision; it does **not** assert that a firmware-specific licence was found.

- **Origin:** Amazon Fire HD8 8th-generation source package
- **Known community precedent:** `postmarketOS/linux-amazon-biscuit`, using the
  `echo-pmos/amazon-biscuit-kernel` lineage at commit
  `3923647bdeca8fdaeb943fb31234dbe361668d28`; that lineage embeds and publishes
  kernels containing this exact blob.
- **Git blob SHA-1:** `a6eafb423215f1317df7af97822cc20858a8ae46`
- **Size:** `30,964` bytes
- **SHA-256:** `77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6`
- **Firmware-specific licence:** no firmware-specific licence found
- **Redistribution decision:** documented-good-faith, following established
  community precedent including postmarketOS `linux-amazon-biscuit`

The exact hash above is the known-good identity contract. Any future swap must
be treated as a release change and rejected unless the replacement matches the
recorded size and SHA-256 or this decision is explicitly re-reviewed.

The candidate retains `license=NOASSERTION` to avoid converting package-level
Linux GPL metadata or community practice into a firmware-specific licence claim.
