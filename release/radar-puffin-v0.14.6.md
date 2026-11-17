# LibreEcho radar-puffin v0.14.6

LibreEcho 0.14.6 replaces 0.14.5 and includes everything in it (see
[the 0.14.5 note](radar-puffin-v0.14.5.md)). It is a feature release. It adds
an equaliser, a light-ring music animation that follows the beat, alarms with
repeat days and snooze, spoken reminders, do not disturb, a time zone setting,
more voice controls and more Home Assistant entities. Most of it closes gaps
from the feature comparison tracked in Product #297. It also fixes AirPlay
jumps and tails, Sendspin dropouts, Wi-Fi pauses and several update problems.

Features marked **experimental** are off by default. [Validation](#validation)
says what was tested on which model.

## Equaliser

**Audio** has a new **Equaliser** card (LibreEcho-UI #539, LibreEcho-Platform
#309, #310, #324):

- Four sliders from −6 to +6 dB in 0.5 dB steps: **Bass** (120 Hz), **Low mid**
  (400 Hz), **High mid** (2.5 kHz) and **Treble** (6 kHz).
- Presets: Flat, Bass boost, Treble boost, Vocal, Loudness, Late night, Warm.
  Moving a slider switches to Custom.
- **Speaker boost:** off, +3 dB or +6 dB. The boost sits in front of the
  speaker protection, so the loudest peaks stay at the same ceiling; it raises
  quiet and mid-level sound.
- Changes apply while music plays, with a short crossfade and no click. The
  settings survive restarts and are included in the configuration export.
- Flat with no boost is the default, and sounds exactly as 0.14.5 did.

## Light ring

- **Music animation that follows the beat.** The music animation used to
  leave about half the ring lit and creeping round slowly. It now turns with
  the beat, reacts to kicks and goes dark between hits. The audio engine sends
  its music features about 23 times a second instead of about 12
  (LibreEcho-UI #538, LibreEcho-Platform #323).
- **Mood colours.** The colours follow the mood of the music: blues and teals
  for calm music, neon for energetic music, reds and oranges for loud, driving
  music (LibreEcho-UI #538).

## Alarms, reminders and time

- **Alarms.** Set, list and cancel alarms on the **Timers** page, through the
  API, or by voice ("set an alarm for 7am", "cancel my alarm"). A time said
  without am or pm is asked about, not guessed. Alarms survive restarts
  (LibreEcho-UI #491).
- **Repeat days and snooze.** An alarm can repeat daily, on weekdays, at
  weekends or on chosen days, and keeps its local time across clock changes.
  Snooze (5 minutes) works by voice, with the action button or through the API
  (LibreEcho-UI #495).
- **Spoken reminders.** "Remind me to take the pills at 6pm", or set one on the
  **Timers** page. At the due time the device chimes and says the reminder.
  Reminders can repeat and survive restarts (LibreEcho-UI #497). In Home
  Assistant voice mode the speech service now stays running, so reminders and
  Home Assistant's *announce* action are spoken instead of staying silent
  (LibreEcho-UI #529, LibreEcho-Platform #322).
- **Sunrise light.** The ring can fade up from dark red to warm white 5 to 30
  minutes before an alarm. It is off by default and is set through the API
  (`PUT /api/v1/alarms/sunrise`); there is no control-centre switch yet
  (LibreEcho-UI #498).
- **Do not disturb.** A switch on the **LED** page silences chimes, button
  tones and Home Assistant announcements. Alarms still ring, and the ring shows
  the do-not-disturb pattern while it is on (LibreEcho-UI #492).
- **Time zone.** Pick a time zone in **Device** settings or in the setup
  wizard, which starts from your browser's zone. Optionally Home Assistant can
  set it over the ESPHome connection (off by default); it is then shown by its
  usual name, such as `Europe/London` (LibreEcho-UI #457, #528). Alarms and
  reminders follow a change straight away instead of after the next restart
  (LibreEcho-UI #537).
- **Time servers.** Set your own NTP servers in **Device** settings, for
  example your router. Leaving the field empty goes back to the defaults
  (LibreEcho-UI #536, Product #369).

## Voice

- **Cancel a request.** While the device is listening, thinking or replying
  to a Home Assistant request, a press of the action button or the new
  **Cancel request** button in Home Assistant cancels it. Saying the wake word
  again while it is thinking also cancels (LibreEcho-UI #452).
- **"Stop" without the wake word.** While a timer or alarm rings or a reply
  plays, saying just "stop" ends it. Off by default; on-device recogniser
  only, so no audio leaves the device for it (LibreEcho-UI #456).
- **Action button.** **Start listening** now starts a request without the wake
  word, **Play / pause** pauses and resumes the radio or a sleep sound, and a
  long press can have its own action (LibreEcho-UI #462).
- **Separate reply volume.** Replies, announcements and chimes can have their
  own volume, set on the **Audio** page or from Home Assistant. Until you set
  one they follow the main volume as before (LibreEcho-UI #455,
  LibreEcho-Platform #307).
- **Your own sounds.** Upload your own listening chime, a new
  finished-listening chime and the timer and alarm ring, preview them, and
  switch back to the built-in sounds at any time (LibreEcho-UI #463).
- **Your own wake word models.** Upload, select and remove wake word models in
  the control centre. A model that fails the checks is refused and nothing
  changes. The [wake word training guide](../docs/wake-word-training.md) and its
  host tool explain how to make one (LibreEcho-UI #478, Product #345).
- **Microphone mixing.** **Audio › Microphone mixing** chooses how the
  microphones are combined for voice: the beamformer (default), an average, or
  a single microphone, which you can pick (LibreEcho-UI #468, #474).
- **Nearest device answers.** Paired devices can let only the one that heard
  the wake word loudest answer (**Network › Nearest device answers**, off by
  default; LibreEcho-UI #465).
- **Noise suppression (experimental).** A switch on the **Audio** page removes
  steady background noise from the speech sent to the assistant. Off by
  default (LibreEcho-UI #460).
- **Wake near-miss clips (experimental).** You can opt in to keeping short
  clips of sounds that almost woke the device, then play, download or delete
  them on the **Privacy** page. Off by default (LibreEcho-UI #490).
- **Second wake word (experimental).** A second wake word model can listen
  alongside the first; both start the same assistant. Off by default, with no
  control-centre setting yet (LibreEcho-UI #480).

## Home Assistant (ESPHome)

- **New entities:** an ambient light sensor in lux (LibreEcho-UI #464); events
  for button presses, long presses and double presses (#467) and for voice
  steps, playback and timers (#469); the LED ring as a light with Solid, Pulse
  and Flash effects (#473); switches and sliders for the wake word, volume,
  reply volume, wake sensitivity and LED brightness (#477); the last wake word
  as a text sensor (#487).
- **Device actions** for automations: `announce`, `play_sound` and `set_ring`
  (LibreEcho-UI #483).
- **Opt-in sensors**, off by default and set in the web configuration file
  only: the last transcript and reply (#487) and a room sound level in dBFS
  (#489).
- **Replies stream.** Home Assistant voice replies start playing while they
  are still being generated, without the speaker power-up delay or a crack
  before the reply (LibreEcho-UI #530). Announcements now play even when their
  pre-announce chime cannot be fetched (#535).
- **Server-side wake word (experimental).** Home Assistant can detect the wake
  word on a continuous microphone stream instead of the device. Off by default
  and set in the web configuration file only; the ring pulses while audio is
  being sent (LibreEcho-UI #486).
- **Bluetooth proxy (experimental).** A first step: passive forwarding of
  Bluetooth LE advertisements to Home Assistant. Off by default and set in the
  web configuration file only (LibreEcho-UI #496).

## Several devices

- **Device list.** **Network** lists the other LibreEcho devices on your
  network, with room, version and a link to each one. A switch stops this
  device being listed (LibreEcho-UI #453).
- **Pairing.** Pair your devices with a shared code on the **Network** page.
  Paired devices sign what they send each other, and the list shows which are
  paired (LibreEcho-UI #459).
- **Groups, copy settings and broadcast**, for paired devices, through the API
  only for now: mute, unmute or stop a group of devices (#470); copy voice,
  audio, Home Assistant or LED settings to another device (#471); and speak a
  short announcement on every paired device or one of them (#488).

## Other settings

- **Interface language.** The control centre can be shown in German, chosen
  under **Appearance**. The choice is kept per browser and follows the
  browser's language by default (LibreEcho-UI #485).
- **Your own HTTPS certificate.** Install your own certificate for the
  control centre, and trust a private certificate authority for outgoing
  HTTPS (LibreEcho-UI #482, #518). The built-in self-signed certificate is now
  kept across restarts, so clients that pin it keep working (#523).
- **Sendspin: Play without pairing.** A new switch on the Sendspin card, off
  by default: Music Assistant servers that have not paired cannot play to the
  speaker unless you turn it on (LibreEcho-UI #476).
- **Privacy wording.** The Privacy page and setup now say clearly that health
  and crash reporting do not exist yet and nothing is sent either way. Your
  saved choices are kept (LibreEcho-UI #454).

## Fixes

- **AirPlay:**
  - playback no longer jumps every few minutes, and groups keep better sync
    (LibreEcho-Platform #314);
  - skip, seek and pause take effect at once, and Stop or Pause no longer
    plays a short tail of the old song (LibreEcho-Platform #315, #317);
  - the speaker reports its delay correctly while the output starts, so
    playback starts and resumes in time (LibreEcho-Platform #319, #320);
  - AirPlay is listed again within seconds after the discovery service
    restarts, instead of disappearing until a reboot (LibreEcho-UI #461).
- **Sendspin:** playback resumes by itself after the audio engine drops the
  stream, for example after a voice reply or a mute, and after the player
  restarts, instead of staying silent until the device restarts
  (LibreEcho-UI #507, #521, #525).
- **Wi-Fi:** pauses of 10–30 seconds that could interrupt streaming and voice
  replies are fixed (LibreEcho-Linux #13). Hidden networks can be joined by
  entering their name (LibreEcho-UI #499).
- **Updates:**
  - an update interrupted by a restart while the new version was being
    confirmed no longer blocks later updates with "update busy"
    (LibreEcho-Platform #321);
  - after **Install update**, **System › System update** offers **Restart into
    update** again and shows the running slot (LibreEcho-UI #524, #528).
- **Voice:**
  - turning on the on-device assistant waits for the voice pipeline instead of
    failing with "Voice assistant service is unavailable" (LibreEcho-UI #527);
  - saving settings no longer leaves two assistant processes running, which
    answered every request twice (LibreEcho-UI #466, Product #344);
  - the wake-word echo canceller resets when music starts or its reference
    stream has gaps (LibreEcho-UI #519, #526).
- **Bluetooth:** turning Bluetooth on from the **Bluetooth** page stays on
  after a reboot (LibreEcho-UI #500), and the SBC codec settings from a phone
  are read correctly (#506).
- **Discovery service:** restarting it from a shell or after an update no
  longer loses AirPlay, Sendspin and ESPHome discovery or leaves the old copy
  running (LibreEcho-Platform #312, #313; LibreEcho-UI #508, #532).
- **Microphone:** a microphone stream that cannot open now reports an error
  instead of an empty stream (LibreEcho-UI #509).

## Guides and tools

- [Going back to stock Fire OS](../docs/install/restore-stock.md) (Product #341).
- [What keeps working when Home Assistant is down](../docs/without-home-assistant.md)
  (Product #343).
- [A porting guide](../docs/porting/README.md) for adding another Echo model
  (Product #342).
- Optional rootless Podman containers for the flashing tools (Product #370),
  and a keyed pogo plug with pin-out for the Echo 2nd Gen (Product #371).

## Upgrading

- **From 0.14.0 – 0.14.5:** update over the air. In the web UI open
  **System › System update**, choose **Check for updates**, then
  **Install update**, then **Reboot**. Do not upload the update file by
  hand: see [Known issues](#known-issues).
- **From 0.13.x:** reinstall. The 0.13 updater cannot install OTA v3 packages,
  so a 0.13 device reports `downloaded_package_invalid` and stays on 0.13. Use
  the installer in the
  [installation guide](https://github.com/aslater3/LibreEcho/blob/main/docs/install/README.md).
  Reinstalling formats userdata: settings, Wi-Fi credentials, accounts and
  pairings are not kept.
- **Echo 2nd Gen (`radar_puffin`) on the old boot chain:** if
  `fastboot getvar product` reports `BISCUIT`, or the installer fails with
  `boot_a_x readback hash mismatch`, run the
  [Kaeru migration kit](https://github.com/aslater3/LibreEcho/tree/main/tools/radar-kaeru-migration)
  before reinstalling (`--dry-run` first; `--gpt-fix` if an older copy of the
  kit already converted the chain).

## Known issues

- **Manual update upload cannot install a new build.** Uploading an update
  file under **System › System update** fails for any build the
  device has not already downloaded through **Check for updates**. The page
  reports a signature failure, but the file is fine. Install with **Check for
  updates** on the Stable or Dev channel instead. This also applies to the
  update from 0.14.5. Fix planned for 0.14.7 (LibreEcho-Platform #316).
- **Ring stays "thinking" after a request with no reply.** When Home Assistant
  ends a request without a reply (for example after "never mind"), the ring
  shows *thinking* for up to 2 minutes (LibreEcho-UI #541).
- **Long announcements through `esphome.libreecho_announce` are dropped.** The
  service silently ignores text over 200 characters. Use
  `assist_satellite.announce`, which plays long text (LibreEcho-UI #542).
- **No volume level on the ring during do not disturb or the sunrise light.**
  The volume buttons still change the volume, but the ring does not show the
  level while either holds the ring (LibreEcho-UI #543).
- **AirPlay from Music Assistant** still needs *AirPlay 2 - compatibility
  mode* or *AirPlay 1 (RAOP)*, or Sendspin; see
  [the 0.14.5 note](radar-puffin-v0.14.5.md#known-issues).

## Validation

Host tests for every change, plus a physical Echo 2nd Gen (`radar_puffin`) and
a physical Echo Dot (`biscuit`) running development builds of these sources.

**Echo 2nd Gen**, on the final release candidate before the equaliser card
(UI #539, Platform #324) was added:

- Installed over the air through the control centre (**Check for updates →
  Install update → Restart into update**). It booted
  the new slot, confirmed it, kept the same slot across a further reboot and
  kept the previous slot as the rollback target.
- Light ring music animation with AirPlay music: moved with the beat, colours
  changed with the music, sound clean.
- A daily alarm and a one-off reminder set after a time zone change from the
  picker: both survived a reboot and fired at the right local time without a
  restart. The alarm was snoozed with the action button and the API. The
  sunrise light faded up before the alarm. Do not disturb silenced button
  tones while the alarm still rang.
- In Home Assistant voice mode: the reminder and an `announce` were spoken; a
  long (about 60 s) `assist_satellite.announce` played to the end; a request
  was cancelled with the action button and with the **Cancel request** button;
  a reply played louder than the music with a separate reply volume; button
  press, long-press and double-press events reached Home Assistant; the LED
  ring light was switched from Home Assistant.
- AirPlay from a Mac: pause, resume, stop and next track with no tail of the
  old audio. A phone played music over Bluetooth (SBC).
- Engine bass, treble and boost (set by file, before the equaliser card):
  audible by ear, no distortion, back to stock when removed.
- Experimental clap and smoke-alarm detectors (switched on for the test only):
  6 of 6 claps and a recorded smoke-alarm pattern detected.

**Both units**, on earlier 0.14.6 candidates carrying the same code for these
items: install through the control centre, **Restart into update**, an
interrupted confirmation and a stale update lock recovering at the next boot,
100 of 100 large HTTPS downloads over Wi-Fi with no stall, custom time
servers, an announcement with a failing pre-announce chime, AirPlay
re-advertised after a discovery restart, the Bluetooth switch surviving a
reboot, the self-signed certificate unchanged across a reboot, German
interface, and the privacy wording. On the Echo Dot also: wake model upload,
select and delete, a second wake word, and turning on the on-device assistant.
On the Echo 2nd Gen also: adding and removing a private certificate authority.

<!-- FINAL-BUILD-VALIDATION -->

Not yet tested on hardware, and handed to community testing: long playback
runs (AirPlay for 60 minutes, Sendspin after voice replies, after hours idle
and over long queues, and Sendspin pairing), server-side wake word, wake
near-miss clips, a second wake word on the Echo 2nd Gen, the microphone
settings (mixing, noise suppression), installing your own HTTPS certificate,
the device list, pairing, groups and broadcast, hidden Wi-Fi networks, and the
time zone from Home Assistant. Also not tested: an update straight from 0.14.5
stable, a fresh install or `--reinstall` of 0.14.6, and a forced rollback from
0.14.6.
