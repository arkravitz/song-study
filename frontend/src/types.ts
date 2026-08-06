/** Shared types. These mirror the backend schema; keep them in sync. */

export type EvidenceStrength = 'direct' | 'derived' | 'inferred' | 'speculative'

export interface Evidence {
  kind: string
  method: string
  metric?: string | null
  t0?: number | null
  t1?: number | null
  value?: unknown
  unit?: string | null
  strength: EvidenceStrength
  note?: string | null
}

export interface ActivitySegment {
  start: number
  end: number
  level_db: number
  peak_db: number
  confidence: number
  intensity: number
  onset_slope_db_s: number
  offset_slope_db_s: number
  pan: number
  width: number
}

export interface SourceDescriptor {
  spectral_centroid_hz?: number | null
  spectral_rolloff85_hz?: number | null
  spectral_flatness?: number | null
  rms_db?: number | null
  crest_factor_db?: number | null
  mean_pan?: number | null
  mean_width?: number | null
  role?: string | null
  descriptors: string[]
  tags: [string, number][]
}

export interface Source {
  id: string
  label: string
  family: string
  color: string
  origin: string
  separator_model?: string | null
  confidence: number
  detection_method: string
  descriptor: SourceDescriptor
  segments: ActivitySegment[]
  active_bars: number[]
  mean_pan: number
  mean_width: number
  pan_stability: number
  total_active_seconds: number
  duty_cycle: number
  mean_level_db: number
  max_level_db: number
  n_entries: number
  /**
   * True when the separated stem carries no audible instrument. A separator
   * returns a file per instrument whether or not the track has one, and the
   * empty file holds bleed — so this lane is not evidence of a part and must not
   * be counted as one.
   *
   * Optional because an analysis cached before this field existed will not have
   * it, and a missing field has to read as "audible" so an old song still shows
   * all of its lanes rather than silently losing one.
   */
  is_silent?: boolean
  n_exits: number
}

export interface Section {
  id: string
  start: number
  end: number
  label: string
  label_confidence: number
  label_source: string
  label_alternatives: { label: string; score: number }[]
  repeat_group: number | null
  repeat_rank: number
  /**
   * Macro structure. A detected boundary is not a section: when several adjacent
   * segments end up with the same name they are one formal section with an
   * internal change. `macro_id` groups them, `macro_label` is the numbered name a
   * producer would use ("Chorus 2"), and `macro_part`/`macro_parts` say which
   * detected segment this is within that macro. A cached analysis written before
   * these fields existed has them null, so every read has to tolerate absence.
   */
  macro_id: string | null
  macro_label: string | null
  user_label?: string
  macro_index: number
  macro_part: number
  macro_parts: number
  bar_start: number | null
  bar_end: number | null
  energy_raw: number
  energy_perceived: number
  loudness_lufs: number | null
  source_count: number
  source_ids: string[]
  stereo_width: number
  stereo_correlation: number
  density: number
  dominant_source: string | null
  novelty: number
  description?: string | null
  deltas_from_previous: string[]
}

export type EventKind =
  | 'enter' | 'exit' | 'restart' | 'level_change' | 'filter_change'
  | 'width_change' | 'density_change' | 'texture_change' | 'harmony_change'
  | 'rhythm_change' | 'fill' | 'riser' | 'impact' | 'downlifter' | 'silence'
  | 'resume' | 'pattern_shift' | 'vocal_doubling' | 'space_change' | 'unknown'

export interface ArrangementEvent {
  id: string
  t: number
  end: number | null
  kind: EventKind
  label: string
  source_ids: string[]
  strength: number
  confidence: number
  bar: number | null
  beat_in_bar: number | null
  is_continuous: boolean
  evidence: Evidence[]
  why?: string | null
  tags: string[]
}

export interface EnergyChange {
  t: number
  end: number | null
  direction: 'rise' | 'fall'
  delta: number
  magnitude: number
  bar: number | null
  contributors: [string, number][]
  evidence: Evidence[]
  explanation?: string | null
}

export interface PatternFinding {
  id: string
  kind: string
  title: string
  detail: string
  strength: number
  times: number[]
  source_refs: string[]
  metrics: Record<string, unknown>
  evidence: Evidence[]
}

export interface Claim {
  text: string
  kind: 'observation' | 'interpretation' | 'creative' | 'question' | 'limitation'
  confidence: 'low' | 'medium' | 'high'
  time_refs: number[]
  source_refs: string[]
  section_refs: string[]
  event_refs: string[]
  evidence: Evidence[]
  grounding: 'verified' | 'partially_grounded' | 'ungrounded'
  grounding_note?: string | null
}

export interface Insight {
  id: string
  title: string
  scope: string
  claims: Claim[]
  model?: string | null
  created_at: number
  token_usage?: Record<string, number> | null
  duration_seconds: number
}

export interface ComparisonInsight {
  id: string
  title: string
  a_ref: string
  b_ref: string
  a_label: string
  b_label: string
  added: string[]
  removed: string[]
  level_changes: Record<string, unknown>[]
  claims: Claim[]
  summary?: string | null
  model?: string | null
}

export interface Grid {
  bpm: number
  bpm_confidence: number
  meter: string
  meter_confidence: number
  offset: number
  tempo_drift: number
  is_tangent: boolean
  beats: number[]
  downbeats: number[]
  onsets: number[]
  bpm_curve: [number, number][]
}

export interface CurveMeta {
  name: string
  frame_hz: number
  unit?: string | null
  description?: string | null
}

export interface EnergyInfo {
  raw: CurveMeta | null
  perceived: CurveMeta | null
  components: Record<string, CurveMeta>
  weights: Record<string, number>
  model_notes: string
  norm_lo: number
  norm_hi: number
}

export interface SongSummary {
  /**
   * Instruments that carry audible audio, and sections as a producer counts
   * them. Both are computed on the server, the same way the library list
   * computes them, because recomputing them in the header is how the sidebar and
   * the header came to disagree about the same record.
   */
  audible_sources: number
  macro_sections: number
  id: string
  title: string
  artist: string | null
  album: string | null
  year: string | null
  duration: number
  sample_rate: number
  channels: number
  is_mono_source: boolean
  source_quality: Record<string, unknown>
  file_info: Record<string, unknown>
  beats: {
    bpm: number
    bpm_confidence: number
    meter: string
    meter_confidence: number
    offset_seconds: number
    tempo_drift: number
    is_tangent: boolean
    onset_times: number[]
    onset_strength: number[]
  }
  loudness: {
    integrated_lufs: number | null
    lra: number | null
    true_peak_dbfs: number | null
    plr_db: number | null
    crest_db: number | null
    method: string
  }
  energy: EnergyInfo
  spatial_summary: {
    is_mono: boolean
    mean_pan: number | null
    mean_width: number | null
    mean_correlation: number | null
    /**
     * Per-band means, and the bands themselves.
     *
     * `low_width` and `high_width` were being sent by the server with no
     * declaration here, so nothing could read them without a cast. The bands
     * carry the crossover edges the measurement was filtered with rather than
     * having the frontend write them down, so a label next to a curve cannot
     * come to describe a different band than the one that was measured.
     */
    low_width: number | null
    mid_width: number | null
    high_width: number | null
    bands: {
      /**
       * The union, not `string`, so that a panel indexing `spatial_summary` by
       * `band.key` is checked against the fields that actually exist. With
       * `string` the inspector needed a cast to read the numbers back, and a
       * cast is exactly the thing that lets a renamed field keep compiling.
       */
      key: 'low_width' | 'mid_width' | 'high_width'
      label: string
      lo_hz: number
      hi_hz: number
      curve: string
    }[]
  }
  sources: Source[]
  sections: Section[]
  transitions: Record<string, never>[] | any[]
  events: ArrangementEvent[]
  energy_changes: EnergyChange[]
  separation: Record<string, unknown>[]
  patterns: PatternFinding[]
  insights: Insight[]
  comparisons: ComparisonInsight[]
  fingerprint: Record<string, unknown>
  metrics: Record<string, unknown>
  quality: string
  models_used: Record<string, string>
  stages: Record<string, any>[]
  warnings: string[]
  llm_enabled: boolean
  created_at: number
  curve_names: string[]
  stem_audio: StemFile[]
  grid: Grid
  grounding: { insights: GroundingSummary[] }
}

export interface StemFile {
  model: string
  stem: string
  url: string
  duration: number
  sample_rate: number
}

export interface GroundingSummary {
  total_claims: number
  verified: number
  partially_grounded: number
  ungrounded: number
  verified_fraction: number
}

export interface SongListItem {
  id: string
  title: string
  artist: string | null
  album: string | null
  duration: number
  bpm: number
  bpm_confidence: number
  meter: string | null
  /** Instruments that carry audible audio, not separator files. */
  sources: number
  /** Sections as a producer counts them: macros, not detected segments. */
  sections: number
  /** The raw counts, kept so the UI can explain the difference when it shows. */
  raw_sources: number
  raw_sections: number
  events: number
  llm_enabled: boolean
  created_at: number
  source_path: string | null
}

export interface JobInfo {
  id: string
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  song_id: string | null
  title: string | null
  stage: string | null
  progress: number
  message: string
  error: string | null
  stages: { name: string; status: string; progress: number; message: string; duration?: number }[]
  started_at: number
  finished_at: number | null
  elapsed: number
  /**
   * A stop has been asked for and has not taken effect yet.
   *
   * Cancellation is only noticed at a stage boundary, so between the click and
   * the job actually stopping there is a window in which the honest thing to
   * show is "stopping", not "running". The flag used to be withheld from the
   * serialisation entirely, so the client could not tell the two apart.
   */
  stopping?: boolean
}

export interface HealthInfo {
  ok: boolean
  llm_enabled: boolean
  llm_model: string | null
  quality: string
  separator: string
  data_dir: string
  hardware: Record<string, unknown>
}
