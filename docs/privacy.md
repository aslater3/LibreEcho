# Privacy and telemetry

LibreEcho processes your voice, your home and your settings on the device.
The only things it sends without being asked are listed here. Every item can
be inspected on the device under **Privacy**.

## Weekly active-device count (always on)

Once a week LibreEcho sends one anonymous ping to `stats.libreecho.org`,
purely to count how many devices are running and on which version. This ping
cannot be turned off. It is how the project knows whether a release is
actually in use.

The ping is a single HTTPS `POST` to `https://stats.libreecho.org/v1/ping`
with `User-Agent: libreecho-ping/1` and this JSON body:

| Field | Example | Meaning |
|---|---|---|
| `v` | `1` | Schema version |
| `hw` | `radar` | Hardware model (`radar` or `biscuit`) |
| `ver` | `0.14.0` | LibreEcho version |
| `ch` | `stable` | Update channel (`stable` or `dev`) |
| `build` | `cc238ba` | Short build hash. Development builds only; absent on stable |
| `wk` | `2026-W41` | ISO week, UTC |
| `mo` | `2026-10` | Month, UTC |
| `yr` | `2026` | Year, UTC |
| `w`, `m`, `y` | `1` | `1` if this is the device's first ping of that week, month or year |

There is no device ID, serial number, MAC address, install date, account,
location, or anything about you or your home. The device remembers only which
week, month and year it has already been counted for, under `/data`.

The server stores only running totals per period, hardware, version, channel
and build. LibreEcho does not store your IP address, request headers or the
time of each request. Cloudflare processes the request to deliver it, as it
does for any website.

The counts are estimates. A factory reset is counted again, an offline device
is not counted, and the endpoint does not authenticate genuine devices, so the
numbers can be inflated by anyone who sends fake pings.

## Help improve LibreEcho (on by default for new setups, can be turned off)

Two separate options appear during setup and on the Privacy page:

- **Health and usage:** anonymous weekly totals about reliability and which
  features are used.
- **Crash reports:** anonymous crash signatures. A full log is only sent if
  you choose to send one.

Neither includes audio, transcripts, Wi-Fi names or device identifiers. New
setups start with both ticked; upgrading from an earlier release keeps the
choices you already made.

In 0.14 these options record consent only: the release does not send health,
usage or crash data. They decide whether a future release may.

## Everything else

Update checks, remote AI services, Home Assistant and other integrations only
contact the network when you start them or turn them on.
