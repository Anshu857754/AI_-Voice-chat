// useDebugState - room/routing/latency snapshot for the optional debug panel.
//
// Fed by the `roxstar.state` topic (app/orchestrator.py::_publish_state),
// republished after every routing decision and bot state change. This is
// real, observed data from the running pipeline - the same numbers the
// backend logs under [LATENCY] - never invented client-side (spec section 19).

import { useEffect, useState } from 'react'
import { onBus, BUS_EVENTS } from '../services/livekit'

export function useDebugState(room) {
  const [state, setState] = useState(null)

  useEffect(() => {
    if (!room) {
      setState(null)
      return undefined
    }
    const offState = onBus(room, BUS_EVENTS.STATE, setState)
    const offDisconnected = onBus(room, BUS_EVENTS.DISCONNECTED, () => setState(null))
    return () => {
      offState()
      offDisconnected()
    }
  }, [room])

  return state
}
