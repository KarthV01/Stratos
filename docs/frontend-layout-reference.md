# Archived frontend layout reference

This note preserves the visual direction of the removed Stratos monitor and distributed-systems field guide. It is a design reference only; no frontend runtime is retained.

## Visual language

- Dark editorial/technical interface with a near-black background (`#171816`), warm off-white text (`#e9e4d8`), charcoal panels (`#1d1e1b`), and thin square-edged borders (`#373832`).
- Accent colors: slate blue `#7392af`, rust `#b86645`, sage `#849b83`, amber `#b69a63`, and muted red `#be705d`.
- Typography mixed Manrope for primary UI and large headlines, Newsreader italic for expressive phrases, and DM Mono for labels, metadata, statuses, and numbering.
- Large, tightly tracked headlines; restrained body copy; uppercase monospaced micro-labels with generous letter spacing.
- No rounded cards or decorative shadows. Hierarchy came from spacing, one-pixel rules, muted text, and limited accent color.
- Desktop content width was capped at 1,240 px with 24 px outer gutters. Motion was subtle and disabled for `prefers-reduced-motion`.

## Shared shell

- A 74 px header with `STRATOS / …` wordmark on the left and compact navigation on the right.
- A thin rule below the header and above a two-part footer.
- Footer labels used tiny monospaced uppercase text, with the system name left and a short three- or four-word thesis right.

## Task monitor layout

1. Editorial hero: left side had the kicker “Distributed work, made visible,” a two-line headline “Give work. / Take results.”, and explanatory copy. The second line was blue, italic serif.
2. Thesis aside: a narrow right column with “Give ↔ Take,” a short queue explanation, and a colored live-connection dot.
3. Topology panel: controller → Redis Streams → a three-card worker stack. Nodes were outlined rectangles connected by one-pixel arrow lines.
4. Working area: a two-column split divided by a vertical rule. The left column contained the task form; the wider right column contained task counts and a ledger.
5. Task rows: status pill, truncated message plus task ID, and right-aligned worker name. Queued/running/completed/failed states used neutral/amber/sage/red.

On screens below 850 px, the hero, topology, and working area stacked vertically. Below 560 px, workers and form fields became single-column and task metadata wrapped under the message.

## Field-guide layout

1. A large hero read “Every system must / give and take.” with the same blue italic serif treatment.
2. Four equal premise cards summarized Delivery, Ownership, Time, and Truth.
3. The body used a sticky 225 px study index on the left and long-form studies on the right.
4. Each study combined oversized title and blue serif summary with an outlined message-flow demonstration.
5. Flow rows showed source, arrow, destination, and message; failure, warning, and protected states used red, amber, and sage.
6. A lower split panel paired ordered safeguards with a concise guarantee.

The overall feel was serious, sparse, and editorial—closer to an infrastructure field manual than a conventional SaaS dashboard.
