/**
 * The timeline: canvas for the dense layers, DOM overlay for the interactive
 * parts (section bands, event pins, selections, labels).
 *
 * Interactions, all of them deliberate:
 *   click / drag      seek + scrub
 *   shift+click       seek to the nearest grid line
 *   shift+drag        zoom to a range
 *   shift+alt+drag    select range A
 *   alt+drag          select range B (for A/B compare)
 *   wheel             zoom about the cursor
 *   wheel             zoom around pointer
 *   double-click      zoom to fit
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ArrangementEvent, EnergyChange, Section, SongSummary } from '../types'
import { api } from '../api'
import {
  EVENT_COLORS, eventKindLabel, fmtBar, fmtBarOnly, fmtTime,
  groundingColor, titleCase,
} from '../lib/format'
import { drawableEvents } from './eventRanges'
import { LABEL_PADDING_PX, LABEL_PX_PER_CHAR, macroSections } from '../lib/sections'
import { EditableSectionName } from './EditableSectionName'
import { boundaryTime, gridStepSeconds, MIN_SECTION_SECONDS } from '../lib/sectionEditing'
import {
  GUTTER, LAYOUT, computeStretch, lanesHeight, laneRects, rowRects,
  setCurveRowError, useTimelineCanvas, type TimelineGeom, type WidthBand,
} from './TimelineCanvas'
import { useCurve, useCurves, usePeaks, useStemWaves, type CurveSeries } from '../hooks/useData'

export interface TimelineProps {
  song: SongSummary
  /**
   * The playhead as a live ref, not a number.
   *
   * A number here is a snapshot of the audio clock taken when React last
   * rendered, and that render is gated at 20 Hz. The canvas drew that
   * snapshot, so the playhead trailed the sound by a median of 32 ms and up
   * to 59 ms — measured in the browser over 80 animation frames. Passing the
   * ref lets the canvas read the clock inside its own draw.
   */
  playheadRef: { current: number }
  playbackTime: number
  onMergeSections: (rightMacroId: string) => Promise<void>
  onSplitAtPlayhead: () => Promise<void>
  onMoveSectionBoundary: (id: string, at: number) => Promise<void>
  onRenameSection: (section: Section, name: string | null) => void
  onUndo: () => void; onRedo: () => void
  undoLabel?: string; redoLabel?: string
  sectionEditBusy: boolean
  sectionEditError: string | null
  onSeek: (t: number) => void
  onScrub?: (t: number) => void
  onSelectEvent: (e: ArrangementEvent | null) => void
  onSelectSection: (s: Section | null) => void
  selectedEventId: string | null
  selectedSectionId: string | null
  view: { t0: number; t1: number }
  setView: (v: { t0: number; t1: number }) => void
  zoomAt: (factor: number, anchorT?: number) => void
  panTo: (t: number) => void
  showAll: () => void
  showRange: (a: number, b: number) => void
  rangeA: { start: number; end: number } | null
  rangeB: { start: number; end: number } | null
  onRangeChange: (a: { start: number; end: number } | null,
                   b: { start: number; end: number } | null) => void
  hiddenSources: Set<string>
  collapsedFamilies: Set<string>
  focusSource: string | null
  onFocusSource: (id: string | null) => void
  /** Mixer, so the lane labels can offer solo/mute where a producer looks. */
  mix?: {
    solo: Set<string>
    loading: Set<string>
  }
  /** Adds or removes a stem from the solo set. */
  onSolo?: (id: string) => void
  /** Selecting an energy-change marker opens its explanation. */
  onEnergyChange?: (c: EnergyChange) => void
  options: {
    showWaveform: boolean
    showEnergyRaw: boolean
    showWidth: boolean
    gridSnap: boolean
    showEvents: boolean
  }
  onOptions: (o: Partial<TimelineProps['options']>) => void
}

interface TooltipState {
  x: number
  y: number
  kind: 'event' | 'section' | 'lane' | 'time' | 'energy'
  event?: ArrangementEvent
  section?: Section
  lane?: { id: string; label: string; color: string }
  t?: number
}

/** Stable identity: a fresh [] on every render would retrigger the draw. */
const EMPTY_CHANGES: EnergyChange[] = []


