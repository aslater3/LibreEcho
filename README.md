# LibreEcho

LibreEcho is an open embedded voice-assistant operating system focused on
privacy, repairability, and local control.

This repository is the product home for the project. Source code and hardware
bring-up remain in the component repositories:

- [LibreEcho-Kernel](https://github.com/aslater3/LibreEcho-Kernel) contains the MT8163 ARM32 kernel, initramfs, image pipeline, hardware support, and signed OTA release workflow.
- [LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI) contains the local web control centre, API, service daemons, and UI test suite.

## Documentation

- [Architecture and repository boundaries](docs/repositories.md)
- [Contributing](CONTRIBUTING.md)

## Support

Use the issue tracker for reproducible bugs and hardware problems. Include the
device model, OS version, active slot, relevant logs, and the smallest reliable
reproduction. Do not include Wi-Fi passwords, API tokens, or private keys.

Use Discussions for design questions, setup help, and ideas that are not yet
actionable bugs.

## License

LibreEcho is released under the MIT License. Component repositories may carry
their own copies of the license and additional notices.
