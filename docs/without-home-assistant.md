# What keeps working when Home Assistant is down

LibreEcho runs on the Echo itself. Home Assistant is an optional extra that
connects to the device over the ESPHome native API (TCP port 6053); the device
never waits for it. When Home Assistant is stopped, restarting, or unreachable,
everything the device owns keeps working, and only the features that Home
Assistant itself provides stop.

This page applies to LibreEcho 0.14 on the Echo 2nd Gen (`radar_puffin`) and the
Echo Dot 2nd Gen (`biscuit`).

## Summary

| Feature | Without Home Assistant | Checked with no Home Assistant connection |
|---|---|---|
| Timers set on the device (web **Timers** page or Local voice mode) | Keep running and ring | Yes, Echo Dot and Echo |
| Alarms set on the device | Keep running and ring | Yes, Echo Dot and Echo |
| Dismissing a ringing timer or alarm from the web **Timers** page | Works | Yes (the timer service's dismiss command, which the page uses), Echo Dot and Echo |
| Local voice assistant (**Local** or **Custom** mode): wake word, speech recognition, timers, spoken reply | Works | Timer request through the local assistant: yes, Echo Dot and Echo |
| Internet radio | Works (needs the internet, not Home Assistant) | Yes, Echo Dot and Echo |
| AirPlay from a phone or computer | Works | Receiver running and advertised: yes. Audio stream: see [How this was checked](#how-this-was-checked) |
| Bluetooth from a phone | Works | Bluetooth service answering: yes. No phone was paired for the check |
| Sendspin (Music Assistant multiroom) | Works while the Music Assistant server is running | Not without Music Assistant; see [Sendspin](#sendspin-and-music-assistant) |
| Web control centre, settings, updates | Works | Yes, Echo Dot and Echo |
| Nursery: sleep sounds, white/pink/brown noise, baby monitor | Works | No Home Assistant code path (on-device `audiod`) |
| Buttons, volume, mute, light ring | Works | No Home Assistant code path (on-device `buttond`, `ledd`) |
| Voice in **Home Assistant** mode | Stops: the wake word gets no answer | Code path and host tests |
| Timers set by voice in **Home Assistant** mode | Stop: Home Assistant owns them, and the device drops them when Home Assistant disconnects | Host tests |
| Announcements, media control and entities from Home Assistant | Stop until Home Assistant reconnects | By design |

## Device-owned features

These run entirely on the Echo. None of them talks to Home Assistant, so a Home
Assistant outage does not slow them down or stop them.

- **Timers and alarms.** The on-device timer service (`timerd`) keeps the
  schedule, stores it so it survives a restart, and rings through the device's
  own speaker. Set them from the web **Timers** page or by voice in **Local**
  or **Custom** mode, and dismiss a ringing one from the **Timers** page.
  Alarms need the device clock to be set, which it does from the internet
  (NTP), not from Home Assistant.
- **The local voice assistant.** In **Local** mode, wake word, speech
  recognition, the timer and stop commands, and text-to-speech all run on the
  device. Open-ended questions go to the assistant provider you signed in to,
  which needs the internet but not Home Assistant. In **Custom** mode, speech
  recognition and speech go to the Wyoming Whisper and Piper servers you
  entered; they keep working as long as those servers are up (see
  [If your other services run on the Home Assistant machine](#if-your-other-services-run-on-the-home-assistant-machine)).
- **Internet radio**, USB storage playback and the spoken clock.
- **AirPlay.** The AirPlay receiver runs on the device and is found by your
  phone or computer directly, over mDNS on your network.
- **Bluetooth** audio from a paired phone.
- **Nursery** sleep sounds, noise and the baby monitor stream.
- **The web control centre** at the device's address, including settings,
  logs, diagnostic export and updates. Updates download from
  `dl.libreecho.org`, with GitHub as a fallback.
- **Buttons, volume, mute and the light ring.** The mute button's hardware latch
  always works.

## Features Home Assistant provides

These need Home Assistant and stop while it is down. They come back on their
own once Home Assistant reconnects; you do not need to restart the device.

- **Voice in Home Assistant mode.** In this mode the device still detects the
  wake word, but speech recognition, the conversation agent and the reply all
  run in Home Assistant Assist. With Home Assistant down the wake word gets no
  answer. If you want voice to work without Home Assistant, choose **Local** or
  **Custom** on the **Voice assistant** page.
- **Timers set by voice in Home Assistant mode.** These timers belong to Home
  Assistant: Home Assistant counts them down and tells the device when they
  finish. When Home Assistant disconnects, the device drops them, so a timer
  set this way does not ring if Home Assistant goes down before it is due. Timers
  and alarms set on the device itself are separate and are not affected.
- **Announcements, text-to-speech and media playback started from Home
  Assistant**, and the device's media player, mute and volume entities in Home
  Assistant.

## Sendspin and Music Assistant

Sendspin multiroom playback comes from a Music Assistant server. The device
plays whatever that server sends and does not need Home Assistant itself. If
Music Assistant runs separately from Home Assistant, Sendspin keeps playing when
Home Assistant is down. If Music Assistant runs as a Home Assistant add-on, it
stops when that machine stops; AirPlay, Bluetooth and radio still work.

## If your other services run on the Home Assistant machine

"Home Assistant is down" can mean Home Assistant Core restarting or the whole
machine being off. Add-ons such as Music Assistant, Whisper and Piper keep running
while only Core restarts, but stop when the machine is off. If your **Custom**
mode Whisper or Piper servers, or your Music Assistant server, live on the same
machine as Home Assistant, plan for them going down together. **Local** voice
mode has no such dependency.

## How this was checked

On 2026-10-31, on LibreEcho 0.14.5, with no Home Assistant connection to the
device (the ESPHome satellite was not running and nothing was connected to port
6053), on an Echo Dot 2nd Gen and an Echo 2nd Gen:

- a 3-second countdown and an alarm 4 seconds ahead were created through the
  timer service; both reached `ringing` and were dismissed;
- the local assistant was given the text "set a timer for 5 seconds" (the step
  after speech recognition). It answered "5 seconds, starting now.", and the
  timer started and rang;
- an internet radio stream played for 15 seconds, with the station and track
  reported, and stopped;
- the AirPlay receiver reported its engine, mDNS, NQPTP and audio processes running
  and was found by name from another computer on the network. An audio stream
  was not sent: the test computer's AirPlay client cannot drive the device's
  AirPlay 2 receiver, so that step still needs a phone or Music Assistant;
- the Bluetooth service and the web control centre answered.

The Home Assistant mode behaviour was not run on a device. It follows from the
satellite (`libreecho-esphomed`): it starts a voice turn only while a Home
Assistant client is connected, and when that client disconnects it tells the
timer service to drop the Home Assistant timers, while an ordinary pipeline
error keeps them. The LibreEcho-UI host tests `tests/test_esphomed.py` and
`tests/test_esphome_remote_timer.c` cover Home Assistant timer events, the drop
on disconnect and the errors that keep the timers, using the real satellite and
timer service binaries.
