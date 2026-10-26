# Sendspin: multiroom audio with Music Assistant

> **Draft for 0.15.** The LibreEcho side below is implemented and tested on
> hardware with the pinned Sendspin reference server. The Music Assistant
> steps have not yet been checked against a Music Assistant release; they
> will be confirmed (with screenshots) before 0.15 ships.

LibreEcho can join [Music Assistant](https://music-assistant.io/) as a
Sendspin speaker. Music Assistant then plays to it on its own or in a group
with other Sendspin speakers, kept in time with each other.

## What you need

- LibreEcho 0.15 or later on an Echo (2nd generation) or Echo Dot. The
  player ships in the audio feature; the integration card says *Not
  installed in this image* when it is missing.
- Music Assistant on the same local network as the speaker. Discovery uses
  mDNS (`_sendspin._tcp`), so multicast must pass between them; Wi-Fi
  "client isolation" or separate VLANs without an mDNS reflector stop it.
- The speaker accepts Sendspin connections on TCP port 8928.

## Turn it on

1. Open the speaker's web page and go to **Integrations**.
2. Tick **Sendspin (Music Assistant)** and press **Save**. It is off by
   default.
3. The card shows the player state:

   | Card says | Meaning |
   |---|---|
   | Starting the player… | Enabled, the player is starting. |
   | Waiting for Music Assistant. This speaker is discoverable on your network. | Ready and advertised; no server connected yet. |
   | Connected to *server* (not paired yet) | A server connected but has no stored pairing. |
   | Connected to *server* | Paired and connected. |
   | Playing from *server* | Audio is playing. |
   | Not configured | The integration is off. |

## Pair it with Music Assistant

The speaker only plays for a server it is paired with.

1. On the Sendspin card press **Show pairing token**, then **Copy**. The
   token starts with `SP:`. Anyone who has it can pair a server with this
   speaker, so keep it private; press **Hide** when you are done. The token
   is never shown in status, logs or a configuration export.
2. In Music Assistant, add the discovered Sendspin player and paste the
   token when it asks for one.
3. The card changes to *Connected to …*. Pairing is stored on the speaker
   and survives restarts.

## Using it

- **Groups.** Create a group of Sendspin players in Music Assistant. Each
  LibreEcho speaker corrects for its own output delay (including its speaker
  processing), so grouped speakers play together: an Echo (2nd generation)
  and an Echo Dot side by side measured within 0.05 ms of each other.
- **Starting.** Playback starts about 1.5 seconds after you press play,
  mostly while the speaker's amplifier powers up.
- **Volume.** Sendspin volume *is* the speaker volume. A change in Music
  Assistant moves the speaker volume like the volume buttons, and the
  buttons, web page or AirPlay volume are reported back to Music Assistant.
- **Mute.** Muting the speaker in Music Assistant silences Sendspin music
  only. Alarms, timers and voice replies still play.
- **Alarms.** An alarm takes over the speaker while it sounds; Sendspin
  audio is silenced for that time.

## Limits

- Audio format: uncompressed PCM, 48 kHz, 16-bit stereo. FLAC and Opus are
  not supported in 0.15; Music Assistant sends PCM to this player.
- One server at a time.
- There is no **Unpair** control yet. Turning the integration off stops the
  player and removes it from the network, but keeps the pairing.

## Turn it off

Untick **Sendspin (Music Assistant)** on the Integrations page and press
**Save**. The player stops and is no longer advertised on the network.

## Troubleshooting

- **Music Assistant does not find the speaker.** Check that the card says
  *Waiting for Music Assistant*, that both are on the same network, and
  that multicast/mDNS is allowed between them (see *What you need*).
- **The card stays on *Starting the player…*.** The audio feature may still
  be starting after boot; wait a minute. If it stays, download a diagnostic
  bundle from the System page and include it in a bug report.
- **Connected but no sound.** Check the speaker volume and that it is not
  muted in Music Assistant, and that no alarm is sounding.

For developers: the REST endpoints are `GET /api/v1/sendspin`,
`POST /api/v1/sendspin/pairing` and `PUT /api/v1/integrations/sendspin`
(see the UI repository's `docs/API.md`); the design is in
[`docs/architecture/sendspin.md`](../architecture/sendspin.md).
