// LiveKit connection service.
//
// This is the ONLY module that touches the livekit-client SDK directly.
// Hooks and components go through the small API exported here, which keeps
// the LiveKit wiring in one place and makes the topics/shapes explicit:
//
//   lk.chat             room text chat   (human <-> bot, mirrors app/agents/base.py)
//   roxstar.transcript  turn-by-turn transcript + interim captions (JSON)
//   roxstar.state       bot states, routing decision, metrics snapshot (JSON)
//
// These three topics match exactly what the Python backend
// (app/orchestrator.py, app/livekit_rt/events.py) publishes and subscribes to.
//
// Events are relayed through a plain `EventTarget` attached to the room
// (`room.roxstarBus`) rather than a one-shot callback object. React hooks in
// this app mount AFTER connectToRoom() resolves (App only renders <Room>
// once `isConnected` is true), so a "destructure callbacks once at connect
// time" design would silently drop every event for every hook - nothing
// would ever have been listening yet. An EventTarget lets any number of
// hooks subscribe independently, at any time, without clobbering each other.

import {
  Room,
  RoomEvent,
  Track,
  ConnectionState,
  createLocalAudioTrack,
} from 'livekit-client'

export const TOPICS = {
  CHAT: 'lk.chat',
  TRANSCRIPT: 'roxstar.transcript',
  STATE: 'roxstar.state',
  CONTROL: 'roxstar.control',
}

export const BUS_EVENTS = {
  PARTICIPANTS: 'participants',
  ACTIVE_SPEAKERS: 'activeSpeakers',
  CONNECTION_STATE: 'connectionState',
  TRANSCRIPT: 'transcript',
  STATE: 'state',
  CHAT: 'chat',
  DISCONNECTED: 'disconnected',
}

const textDecoder = new TextDecoder()

/**
 * Fetch a config + token from the backend and open a LiveKit connection.
 *
 * @param {object} opts
 * @param {string} opts.tokenEndpoint - backend base URL (VITE_TOKEN_ENDPOINT)
 * @param {string} opts.displayName
 * @param {string} [opts.room]
 * @param {string} [opts.identity] - reuse across refreshes to keep memory
 * @returns {Promise<{ room: Room, identity: string, livekitUrl: string }>}
 */
export async function connectToRoom({ tokenEndpoint, authToken, displayName, room, identity }) {
  const tokenRes = await fetch(`${tokenEndpoint}/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
    body: JSON.stringify({ display_name: displayName, room, identity }),
  })
  if (!tokenRes.ok) {
    const body = await tokenRes.json().catch(() => ({}))
    const err = new Error(body.detail || `Token request failed (${tokenRes.status})`)
    err.status = tokenRes.status
    throw err
  }
  const { token, url, identity: issuedIdentity } = await tokenRes.json()

  const lkRoom = new Room({
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })
  lkRoom.roxstarBus = new EventTarget()

  attachBus(lkRoom)

  await lkRoom.connect(url, token, { autoSubscribe: true })
  // Called from the Join click (a user gesture) so the browser allows playback.
  await lkRoom.startAudio().catch(() => {})

  return { room: lkRoom, identity: issuedIdentity, livekitUrl: url }
}

/** Subscribe to a bus event; returns an unsubscribe function. */
export function onBus(room, event, callback) {
  if (!room?.roxstarBus) return () => {}
  const listener = (e) => callback(e.detail)
  room.roxstarBus.addEventListener(event, listener)
  return () => room.roxstarBus.removeEventListener(event, listener)
}

function emit(room, event, detail) {
  room.roxstarBus.dispatchEvent(new CustomEvent(event, { detail }))
}

/**
 * Wire every LiveKit room event this app cares about onto `room.roxstarBus`.
 * Keeping this as one function makes it obvious exactly which SDK events the
 * UI reacts to, and keeps React components free of SDK event-name strings.
 */
function attachBus(room) {
  const emitParticipants = () => emit(room, BUS_EVENTS.PARTICIPANTS, snapshotParticipants(room))

  room
    .on(RoomEvent.ParticipantConnected, emitParticipants)
    .on(RoomEvent.ParticipantDisconnected, emitParticipants)
    .on(RoomEvent.ParticipantAttributesChanged, emitParticipants)
    .on(RoomEvent.ParticipantNameChanged, emitParticipants)
    .on(RoomEvent.TrackMuted, emitParticipants)
    .on(RoomEvent.TrackUnmuted, emitParticipants)
    // Remote audio (the AI voices) is only played once its track is attached
    // to an <audio> element - subscribing alone produces no sound.
    .on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        const el = track.attach()
        el.dataset.roxstarAudio = 'remote'
        document.body.appendChild(el)
      }
      emitParticipants()
    })
    .on(RoomEvent.TrackUnsubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) track.detach().forEach((el) => el.remove())
      emitParticipants()
    })
    .on(RoomEvent.AudioPlaybackStatusChanged, () => {
      if (!room.canPlaybackAudio) room.startAudio().catch(() => {})
    })
    .on(RoomEvent.LocalTrackPublished, emitParticipants)
    .on(RoomEvent.LocalTrackUnpublished, emitParticipants)
    .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      emit(
        room,
        BUS_EVENTS.ACTIVE_SPEAKERS,
        speakers.map((p) => p.identity),
      )
    })
    .on(RoomEvent.ConnectionStateChanged, (state) => {
      emit(room, BUS_EVENTS.CONNECTION_STATE, state)
    })
    .on(RoomEvent.Reconnected, () => emitParticipants())
    .on(RoomEvent.Disconnected, (reason) => emit(room, BUS_EVENTS.DISCONNECTED, reason))
    .on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== TOPICS.TRANSCRIPT && topic !== TOPICS.STATE) return
      let data
      try {
        data = JSON.parse(textDecoder.decode(payload))
      } catch {
        return
      }
      emit(room, topic === TOPICS.TRANSCRIPT ? BUS_EVENTS.TRANSCRIPT : BUS_EVENTS.STATE, data)
    })

  // Room chat arrives as a text stream on the lk.chat topic (matches the
  // backend's room.local_participant.send_text(..., topic="lk.chat")).
  room.registerTextStreamHandler(TOPICS.CHAT, async (reader, participantInfo) => {
    const text = await reader.readAll()
    const identity = participantInfo?.identity ?? 'unknown'
    emit(room, BUS_EVENTS.CHAT, {
      streamId: reader.info?.id ?? null,
      identity,
      name: room.getParticipantByIdentity(identity)?.name ?? identity,
      text,
      isLocal: identity === room.localParticipant.identity,
      at: Date.now(),
    })
  })
}

/** Plain-object snapshot of every participant, human and bot alike. */
export function snapshotParticipants(room) {
  const all = [room.localParticipant, ...Array.from(room.remoteParticipants.values())]
  return all.map((p) => toParticipantInfo(p, room))
}

function toParticipantInfo(p, room) {
  const attrs = p.attributes || {}
  const isBot = attrs.role === 'bot'
  const micPub = Array.from(p.trackPublications.values()).find(
    (pub) => pub.source === Track.Source.Microphone,
  )
  return {
    identity: p.identity,
    name: p.name || p.identity,
    isLocal: p === room.localParticipant,
    isBot,
    botId: attrs.bot_id ?? null,
    botState: attrs.bot_state ?? null,
    voice: attrs.voice ?? null,
    micEnabled: isBot ? true : !!micPub && !micPub.isMuted,
    hasMicTrack: !!micPub,
    connectionQuality: p.connectionQuality,
  }
}

/** Publish (or unpublish) the local microphone. */
export async function setMicrophoneEnabled(room, enabled) {
  await room.localParticipant.setMicrophoneEnabled(enabled)
}

/** UI -> worker control message (AI<->AI mode, stop). Humans only; the worker checks. */
export async function sendControl(room, payload) {
  await room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(payload)), {
    reliable: true,
    topic: TOPICS.CONTROL,
  })
}

/** Send a room chat message on the shared chat topic. */
export async function sendChatMessage(room, text) {
  await room.localParticipant.sendText(text, { topic: TOPICS.CHAT })
}

export async function disconnectRoom(room) {
  if (!room) return
  await room.disconnect()
}

export function isConnected(room) {
  return room?.state === ConnectionState.Connected
}

export { Room, RoomEvent, Track, ConnectionState, createLocalAudioTrack }
