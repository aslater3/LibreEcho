# LibreEcho installation guide

> For Amazon Echo 2nd generation (`radar_puffin`, MT8163). This procedure
> involves opening the device, soldering, exposed electronics, and entering
> MediaTek BROM mode.

## Safety first

- Unplug the Echo before opening it. Never solder or probe a powered board.
- The eMMC and BROM short point are underneath the heat spreader. The heat
  spreader must be removed to access the marked point.
- Remove and refit the heat spreader carefully. Do not damage the thermal pad or
  allow metal tools to slip across the board.
- Use an insulated probe or purpose-built pogo jig for the short.
- Never short the UART point, USB `D+`/`D-`, a capacitor, inductor, crystal,
  battery contact, or another test pad.

## Equipment

### Required

- Linux computer
- Data-capable USB cable
- Fine-tip temperature-controlled soldering iron
- Fine solder, flux, 30–34 AWG wire, and Kapton tape
- Fine-point pogo pins and a stable pogo-pin jig, if not soldering
- Fine tweezers, plastic spudger, and small screwdrivers
- Magnification and good lighting
- ESD mat/wrist strap or static-safe work surface
- Digital multimeter with continuity mode
- Latest **initial-install bundle** and matching SHA-256 file
- Insulated probe or purpose-built pogo jig for the BROM short

### Optional: developer UART console

UART is **not required to install or flash LibreEcho**. USB is the required
connection for installation. Developers may add UART to watch boot messages or
debug hardware:

- 3.3 V USB-to-TTL serial adapter, adjustable to 921600 baud
- Fine-point pogo pins and a stable pogo-pin jig, or 30–34 AWG wire

Never connect a 5 V UART adapter. For the optional console, connect the board's
UART signal to the adapter's **RX input** and board ground to adapter GND. Leave
adapter TX and VCC disconnected.

## 1. Download and verify the release

1. Download the initial-install bundle from the [latest
   release](https://github.com/aslater3/LibreEcho/releases/latest).
2. Confirm it supports your Echo model and board revision.
3. Verify the checksum:

   ```sh
   sha256sum --check libreecho-*-SHA256SUMS
   ```

4. Read the installation instructions included with the download.

Do not use an OTA archive, random `boot.img`, raw `zImage`, or an installer from
an old issue comment for a first install.

## 2. Open the Echo

1. Disconnect power and USB.
2. Photograph the enclosure, screws, and flex-cable routing.
3. Remove the cover with a plastic tool.
4. Remove the heat spreader to expose the eMMC and the marked BROM short point.
   Keep the thermal pad clean and intact.
5. Photograph the heat-spreader position.
6. Release flex-cable latches before disconnecting cables; never pull on the
   orange flex cable.
7. Place the board on an insulating, static-safe surface.
8. Check that the board matches the photographs. Stop if it does not.

## 3. Enter BROM mode

The eMMC and BROM short point are underneath the heat spreader. Remove the heat
spreader first; the marked point cannot be accessed while it is fitted.

Use the short point shown in the annotated photo. Keep the board unpowered until
the installation instructions tell you to connect power.

![Short point to ground](assets/echo2-short-to-ground.jpg)

1. Disconnect power and USB, leaving the board accessible.
2. Use an insulated probe or purpose-built pogo jig.
3. Touch the marked short point to the marked ground point only.
4. Connect power and USB as instructed by the installer.
5. Wait for the MediaTek BROM device to appear.
6. Remove the short immediately after it appears.
7. Continue with the installer.

Never leave the short in place while writing. If BROM does not appear, remove
power before checking the probe and board orientation.

## 4. Connect USB

The USB connection is made on the **amplifier/tweeter board** at the annotated
`D+`, `D-`, and `GND` pads. These pads must be connected to a USB cable either by
soldering wires or with a stable pogo-pin jig.

![USB D+, D-, and GND pads](assets/usb-dplus-dminus-gnd.jpg)

- Soldering: connect USB `D+`, `D-`, and `GND` to the matching wires in a
  data-capable USB cable.
- Pogo pins: use a jig that holds three separate contacts firmly on `D+`, `D-`,
  and `GND`. Check continuity and USB polarity before applying power.
- Do not connect these pads to a TTL UART adapter.

USB is the required connection for flashing. The installer should detect the
Echo over this USB connection.

## 5. Run the installer

The automated installer is still being developed. Until it is released, follow
the installation instructions supplied with the download:

1. Confirm the checksum passed.
2. Confirm the installer sees the Echo over USB.
3. Follow the on-screen instructions.
4. Do not interrupt USB or power during installation.
5. Follow the installer's reboot instructions.

If installation fails, note the error message and do not try a different image or
partition.

## 6. First boot

1. Remove the shorting tool.
2. Reconnect the flex cables and close the enclosure.
3. Power on the Echo.
4. Confirm the LibreEcho control transport appears.
5. Open the LibreEcho web control centre and complete setup.
6. Change default credentials immediately.
7. Test the features listed for your release.

## Optional developer UART

The UART point is on the **back of the LED-ring board, directly beneath `C7`**.
The annotated USB pads in the other photo are on the **amplifier/tweeter board**.

![UART point beneath C7](assets/uart-rx-board.jpg)

To fit a temporary UART connection:

1. Make sure the board is completely unpowered.
2. Solder a fine wire to the UART point and a separate wire to ground, or use a
   stable pogo-pin jig.
3. Inspect for solder bridges and secure wires with Kapton tape.
4. Connect the UART signal to the adapter's RX input and ground to adapter GND.
5. Leave adapter TX and VCC disconnected.

Use **921600 baud, 8N1** if you want to read the console:

```sh
stty -F /dev/ttyUSB0 921600 cs8 -cstopb -parenb -ixon -ixoff -crtscts raw -echo
cat /dev/ttyUSB0
```

Replace `/dev/ttyUSB0` with the actual adapter device. UART is for observing and
debugging only; it is not part of the normal installation process.

## Troubleshooting

### BROM is not detected

- Remove power and the probe.
- Confirm the short point and ground point.
- Repeat the installation instructions' power/USB order.
- Try another USB cable or USB port without a hub.

### Installer reports a write failure

Stop and note the error message. Do not try a different partition or image. Ask
for help with the release version, board revision, and error message.
