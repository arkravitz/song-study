/** App shell: library + timeline + inspector. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type SectionEditSnapshot } from './api'
import { EditHistory } from './lib/editHistory'
import { Sidebar } from './components/Sidebar'
import { Transport } from './components/Transport'
import { SongStudy } from './components/SongStudy'
import { readUserSectionNames, withUserSectionNames, writeUserSectionNames } from './lib/userSectionNames'
import { macroSections } from './lib/sections'
import { Timeline } from './components/Timeline'
import { EditableSectionName } from './components/EditableSectionName'
import { MIN_SECTION_SECONDS } from './lib/sectionEditing'
import { PanelBoundary } from './components/ErrorBoundary'
import {
  EventDetail, PatternsList, SectionDetail, SongOverview,
} from './components/Inspector'
import { EnergyChangeList, ReasoningPanel } from './components/ReasoningPanel'
import { useHealth, useJob, useJobs, useSong, useSongList } from './hooks/useData'
import { usePlayer, useTransportKeys } from './hooks/useTransport'
import type { StemInfo } from './audio/Player'
import { useZoomView } from './hooks/useZoom'
import {
  loadState, saveState, songViewFor, type PersistedState, type SongView,
} from './lib/persist'
import { EVENT_COLORS, eventKindLabel, eventKindQualifier, fmtTime } from './lib/format'
import type { ArrangementEvent, Section } from './types'

type SectionEditState = { snapshot: SectionEditSnapshot; names: Record<string, string> }

type Tab = 'overview' | 'events' | 'energy' | 'patterns' | 'reasoning'

/** The tabs that exist, for validating a stored value against. */
const TAB_NAMES: readonly Tab[] = ['overview', 'events', 'energy', 'patterns', 'reasoning']

const DEFAULT_OPTIONS = {
  showWaveform: true,
  // Two overlaid energy traces is an analysis view. The perceived curve is the
  // one that answers "where does this build and release".
  showEnergyRaw: false,
  // Off by default. Both answer "why does this feel bigger", which is a real
  // question and the wrong first question. On, they were the second and third
  // loudest things on screen and took a third of the plot height.
  showWidth: false,
  // A flat-topped block that says nothing about where the rhythm is. Off with
  // the other "why" traces; the events row already shows entrances.
  gridSnap: true,
  showEvents: false,
}

