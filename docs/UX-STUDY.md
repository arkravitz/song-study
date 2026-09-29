# Song study redesign

Scope: existing analysis only. No ML or DSP changes.

Initial walkthrough: All the Small Things, then the existing funk recording. Loaded the app before reading frontend code. Explored section selection, playback, instrument lanes, comparison, and energy changes.

The dominant problem is that clicking adds measurements to a report instead of answering the next musical question. Two sidebars compete with the song; repeated section names are ambiguous; the waveform and energy chart outweigh structural landmarks; section details describe only the first detected fragment of a grouped passage.

Design: keep a full-width song map, chronological section names, a single contextual study area, and direct instrument and passage comparison. Use existing source presence, segment activity, section averages, and energy contributors to explain changes without claiming acoustic causation. Keep full analysis available in a drawer. No automatic model calls.

Iterations completed:
- Full-width song map and contextual study panel. The first browser pass exposed a permanent pair of sidebars and a section click that opened six measurements before explaining the music. The revised map leaves the song central and moves evidence into an optional drawer.
- Section hierarchy and comparison. The second browser pass exposed unreadable short-section labels and a comparison that required an extra step. The numbered map now keeps every name readable, and selecting an adjacent repeated section immediately shows its differences and instrument participation.
- Instrument and change explanations. Clicking Bass in "Taste" answers its first entrance (0:17.3) in one step; the first chorus explains Piano joining and Drums playing through more of the passage. The interior drop at 1:55 identifies Bass leaving. The user pointed out that the previous "biggest drop" landed on the final fade, so the prompt now excludes the ending.
- Cold restart on a fresh local server, then a 900×700 check. The tall window revealed unused space under the lanes; the timeline height was capped to make the explanation visible. The compact view keeps the complete structure and current instruments visible, with the study panel scrollable.
- User correction. Renaming the second "Chorus" to "Bridge" updated the map, ribbon, current passage, and subsequent chorus numbering. The name survived a reload; "Use suggested name" restored the analyzed label after the test. Renames are local to this browser.

Producer questions, using the finished interface on "Taste":
1. Bass first enters at 0:17.3: click Bass once.
2. At the first chorus, Piano joins, Drums occupy more of the passage, and the stereo image opens: click Chorus 1 once.
3. Why it feels bigger: the same click shows the source and energy changes together. This remains an inference from detected activity, not a claim of production causation.
4. Chorus 2 versus Chorus 1: click Chorus 2 once; its participation table shows Piano more sustained and Other sounds less present, alongside lower energy and narrower width.
5. Largest interior dropout: click "Biggest energy drop within the song"; the 1:55 passage identifies Bass dropping out. The final fade is no longer selected.
6. Active instruments: read the strip immediately below the timeline at the playhead; click a named part to follow it through the song.
7. Interesting decisions: the two suggested energy moments and related-section links provide starting points without forcing a generated observation feed.

Limitations: detected section roles remain provisional. The saved "Taste" analysis still describes several adjacent passages as choruses, and the saved "All the Small Things" analysis misidentifies repeated choruses. See B-023 and B-035 in BUGS.md; this pass did not change section recognition. "Taste" piano activity also has a known detection problem (B-049), which can affect participation comparisons. User names are stored only in the current browser, not with the analyzed song on the server.
