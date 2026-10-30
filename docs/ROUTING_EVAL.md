# Routing evaluation — held-out paraphrases

This document reports how the deterministic router (`agent_repo/routing.py`) performs on
**20 paraphrased requests that do not appear in any seed agent's `example_queries`**. Each request
is paired with the agent a human would expect. The output below is the **real** result of running
`tests/test_routing_heldout.py` on the **memory backend**; the test reads the accuracy back from
this file so the two cannot drift.

Held-out accuracy: **14/20** (0.700) — no threshold is asserted (baseline before symmetric modality
normalisation and payload-compatibility tie-breaking within the `0.15` margin was **11/20** / 0.550).
The purpose is honest reporting, not a passing bar. `no_match` rows are requests the router declined
to route; `missing_input` (`wrong-then-break`) rows are requests the router refused because no
payload-compatible candidate was within the `0.15` score margin of the top candidate.

| # | Query | Attachment | Expected | Actual | Correct |
|---|-------|-----------|----------|--------|:-------:|
| 1 | boil down this report to its main ideas | — | text_summarizer | no plan (missing_input) | ✗ |
| 2 | distill the essential points from these paragraphs | — | text_summarizer | text_summarizer | ✓ |
| 3 | shorten this long write-up but keep the gist | — | text_summarizer | no plan (missing_input) | ✗ |
| 4 | what are the highlights of this text | — | text_summarizer | text_summarizer | ✓ |
| 5 | produce a visual of a mountain lake at dawn | — | image_generator | image_generator | ✓ |
| 6 | render an illustration of a robot holding a flower | — | image_generator | image_generator | ✓ |
| 7 | I need artwork depicting a futuristic city | — | image_generator | no plan (no_match) | ✗ |
| 8 | make a drawing of a house for my slide | — | image_generator | image_generator | ✓ |
| 9 | shoot this message over to my manager by email | — | email_sender | email_sender | ✓ |
| 10 | deliver this note to the team inbox | — | email_sender | email_sender | ✓ |
| 11 | forward this message as an email | — | email_sender | email_sender | ✓ |
| 12 | write down what is being said here | memo.wav | audio_transcriber | audio_transcriber | ✓ |
| 13 | turn this voice memo into words | memo.wav | audio_transcriber | meeting_note_taker | ✗ |
| 14 | put this speech into written form | memo.wav | audio_transcriber | audio_transcriber | ✓ |
| 15 | capture the action items from this meeting recording | memo.wav | meeting_note_taker | meeting_note_taker | ✓ |
| 16 | tell me what this picture contains | photo.png | image_classifier | no plan (no_match) | ✗ |
| 17 | identify the objects in this photo | photo.png | image_classifier | no plan (no_match) | ✗ |
| 18 | what does this image depict | photo.png | image_classifier | image_classifier | ✓ |
| 19 | report the columns and row counts in this csv | table.csv | file_analyzer | file_analyzer | ✓ |
| 20 | give me an overview of this data file | table.csv | file_analyzer | file_analyzer | ✓ |

## Cause of Set 1 rows 16 & 17 (`picture` / `photo`) vs row 18 (`image`)

On rows 16 (`"tell me what this picture contains"`), 17 (`"identify the objects in this photo"`), and
18 (`"what does this image depict"`), `photo.png` is attached (`payload_modality = Modality.IMAGE`,
`has_attachment = True`). Under the modality-word rule (`extract_facts` in `agent_repo/routing.py`),
every word in `MODALITY_VOCABULARY[Modality.IMAGE]` (`image`, `picture`, `photo`, `photograph`,
`png`, `jpg`, `jpeg`) is **dropped as payload-redundant** before scoring:

- Row 16 drops `"picture"`, leaving `intent_tokens = ('tell', 'contain')` — zero overlap with
  `image_classifier`'s metadata (`no_match`).
- Row 17 drops `"photo"`, leaving `intent_tokens = ('identify', 'object')` — zero overlap with
  `image_classifier`'s metadata (`no_match`).
- Row 18 also drops `"image"`, leaving `intent_tokens = ('does', 'depict')`, and matches
  `image_classifier` only because `"does"` is not in `_STOPWORDS` and appears in
  `"what category does this belong to"` (`image_classifier.example_queries`).

Thus rows 16 and 17 fail **not** because `"picture"` and `"photo"` differed from `"image"`, but
because the attached `photo.png` already supplies the `image` modality so `"picture"` and `"photo"`
are stripped by the modality-word rule, and the remaining verbs/nouns (`tell`/`contain`,
`identify`/`object`) are outside `image_classifier`'s seed vocabulary. When modality words are
**not** dropped (e.g., text requests mentioning `"picture"`, `"photo"`, `"paragraph"`, `"csv"`),
`_stem()` / `tokenize()` now normalises all `MODALITY_VOCABULARY` words symmetrically to their
canonical modality token (`image`, `audio`, `file`, `text`, `json`) in both query terms and agent
metadata terms.

## Set 1 before vs after summary

| Metric | Set 1 Before | Set 1 After |
|---|---|---|
| Correct | **11/20** (`0.550`) | **14/20** (`0.700`) |
| `no_match` (`chosen agent: none`) | `3` (rows 7, 16, 17) | `3` (rows 7, 16, 17) |
| `wrong-then-break` (`missing_input`) | `5` (rows 1, 3, 4, 5, 10) | `2` (rows 1, 3) |
| `silently wrong` (routed to wrong agent) | `1` (row 13) | `1` (row 13) |

Rows 4 (`text_summarizer`), 5 (`image_generator`), and 10 (`email_sender`) moved from
`wrong-then-break (missing_input)` to **correct** once `Router.route()` preferred payload-compatible
candidates within the `0.15` score margin of the top candidate. Rows 1 and 3 remain
`wrong-then-break (missing_input)` because on those two queries `text_summarizer` scored `0.1193`
and `0.1167` (just below `min_score_threshold = 0.12`), leaving no compatible candidate in `scored`.

## Breakdown of the 6 misses (set 1 after)

| # | Query | Attachment | Expected | Miss category | Top-scored (chosen) goal agent | Matched terms (score) | Final route outcome |
|---|---|---|---|---|---|---|---|
| 1 | boil down this report to its main ideas | — | `text_summarizer` | `wrong-then-break` (`missing_input`) | `file_analyzer` | `['report']` (`0.1400`; `text_summarizer` scored `0.1193 < 0.12`) | `no plan (missing_input)` — `file_analyzer` needs `file`, got `text` |
| 3 | shorten this long write-up but keep the gist | — | `text_summarizer` | `wrong-then-break` (`missing_input`) | `meeting_note_taker` | `['write']` (`0.1209`; `text_summarizer` scored `0.1167 < 0.12`) | `no plan (missing_input)` — `meeting_note_taker` needs `audio`, got `text` |
| 7 | I need artwork depicting a futuristic city | — | `image_generator` | `no_match` | `none` | `[]` (`email_sender` scored `0.1176 < 0.12` on `['need']`) | `no plan (no_match)` |
| 13 | turn this voice memo into words | `memo.wav` | `audio_transcriber` | `silently wrong` | `meeting_note_taker` | `['turn']` (`0.1919` vs `audio_transcriber` `0.1887`) | routed to `meeting_note_taker` |
| 16 | tell me what this picture contains | `photo.png` | `image_classifier` | `no_match` | `none` | `[]` (`0.0000`) | `no plan (no_match)` (`picture` dropped by modality-word rule) |
| 17 | identify the objects in this photo | `photo.png` | `image_classifier` | `no_match` | `none` | `[]` (`0.0000`) | `no plan (no_match)` (`photo` dropped by modality-word rule) |

## Second held-out set (20 new paraphrases)

A **second, independent** set of 20 paraphrases — none reused from any seed agent's
`example_queries` and none reused from the first set — is measured by
`tests/test_routing_heldout_2.py`. It was written once, **not inspected or used during Set 1
tuning**, and evaluated once after Set 1 changes were finalised.

> **Provenance note (honesty).** The exact shell command used for the single Set 2 run was **not
> captured** in the session log, so it is not reproduced here and must not be reconstructed. The run
> is reproducible with `.venv\Scripts\python.exe -m pytest tests\test_routing_heldout_2.py -q -m "not neo4j"`
> (the test itself prints `[routing-heldout-2] 16/20 = 0.800 correct (memory backend)` and asserts the
> same number against this document, so the figure cannot silently drift).

Held-out set 2 accuracy: **16/20** (0.800) — no threshold is asserted (prior baseline before the
Set 1 router updates was **12/20** / 0.600: `3 no_match`, `4 wrong-then-break`, `1 silently wrong`).

| Metric | Set 2 Before | Set 2 After (run once) |
|---|---|---|
| Correct | **12/20** (`0.600`) | **16/20** (`0.800`) |
| `no_match` (`chosen agent: none`) | `3` (rows 2, 8, 18) | `2` (rows 8, 18) |
| `wrong-then-break` (`missing_input`) | `4` (rows 5, 6, 7, 11) | `0` |
| `silently wrong` (routed to wrong agent) | `1` (row 14) | `2` (rows 11, 14) |

| # | Query | Attachment | Expected | Actual | Correct |
|---|-------|-----------|----------|--------|:-------:|
| 1 | condense this long article into a few sentences | — | text_summarizer | text_summarizer | ✓ |
| 2 | pull out the main takeaway from this paragraph | — | text_summarizer | text_summarizer | ✓ |
| 3 | give me the short version of this write-up | — | text_summarizer | text_summarizer | ✓ |
| 4 | reduce this document to its key ideas | — | text_summarizer | text_summarizer | ✓ |
| 5 | recap the important parts of this text | — | text_summarizer | text_summarizer | ✓ |
| 6 | sketch a picture of a lighthouse at night | — | image_generator | image_generator | ✓ |
| 7 | design an image of a robot chef | — | image_generator | image_generator | ✓ |
| 8 | paint a scene of a quiet forest | — | image_generator | no plan (no_match) | ✗ |
| 9 | email the weekly update to my colleagues | — | email_sender | email_sender | ✓ |
| 10 | send this summary by mail to the manager | — | email_sender | email_sender | ✓ |
| 11 | dispatch this note to the team | — | email_sender | text_summarizer | ✗ |
| 12 | convert this audio clip into text | memo.wav | audio_transcriber | audio_transcriber | ✓ |
| 13 | transcribe what is on this recording | memo.wav | audio_transcriber | audio_transcriber | ✓ |
| 14 | write out the words from this voice note | memo.wav | audio_transcriber | meeting_note_taker | ✗ |
| 15 | summarize the meeting from this recording | memo.wav | meeting_note_taker | meeting_note_taker | ✓ |
| 16 | make meeting notes from this call recording | memo.wav | meeting_note_taker | meeting_note_taker | ✓ |
| 17 | label the contents of this image | photo.png | image_classifier | image_classifier | ✓ |
| 18 | what objects can you see in this picture | photo.png | image_classifier | no plan (no_match) | ✗ |
| 19 | categorize what this image shows | photo.png | image_classifier | image_classifier | ✓ |
| 20 | report the statistics for this data file | table.csv | file_analyzer | file_analyzer | ✓ |

### Breakdown of the 4 misses (set 2 after)

| # | Query | Attachment | Expected | Miss category | Top-scored (chosen) goal agent | Matched terms (score) | Final route outcome |
|---|---|---|---|---|---|---|---|
| 8 | paint a scene of a quiet forest | — | `image_generator` | `no_match` | `none` | `[]` (`0.0000`) | `no plan (no_match)` |
| 11 | dispatch this note to the team | — | `email_sender` | `silently wrong` | `text_summarizer` (compatible within `0.15` of `meeting_note_taker`) | `['note']` (`0.1866`; `email_sender` scored `0.1536` on `['team']`) | routed to `text_summarizer` |
| 14 | write out the words from this voice note | `memo.wav` | `audio_transcriber` | `silently wrong` | `meeting_note_taker` | `['note', 'write']` (`0.4022`) | routed to `meeting_note_taker` |
| 18 | what objects can you see in this picture | `photo.png` | `image_classifier` | `no_match` | `none` | `[]` (`0.0000`) | `no plan (no_match)` (`picture` dropped by modality-word rule) |

## Known limits: development-set status and silent misroutes

- **Set 1 (`14/20`) and Set 2 (`16/20`) are now development sets, not held-out.** Set 1
  (`tests/test_routing_heldout.py`, **14/20**) and Set 2 (`tests/test_routing_heldout_2.py`,
  **16/20**) have been tuned against and inspected. Neither set is a held-out benchmark anymore;
  reporting an unseen accuracy figure requires a fresh hand-written Set 3.
- **Silent misroutes (`wrong agent, no break`) — known limits:**
  - **Set 1 row 13 (`"turn this voice memo into words"`, `memo.wav`):** expected `audio_transcriber`,
    routed to `meeting_note_taker` (`['turn']` scored `0.1919` on `meeting_note_taker.usefulness` vs
    `0.1887` on `audio_transcriber.usefulness`, and both accept `audio`).
  - **Set 2 row 11 (`"dispatch this note to the team"`, no attachment):** expected `email_sender`,
    routed to `text_summarizer` (`meeting_note_taker` scored `0.3103` on `['note']` but needed
    `audio`; within the `0.15` tie-break margin, `text_summarizer` scored `0.1866` on `['note']` vs
    `email_sender` `0.1536` on `['team']`, and both accept `text`).
  - **Set 2 row 14 (`"write out the words from this voice note"`, `memo.wav`):** expected
    `audio_transcriber`, routed to `meeting_note_taker` (`['note', 'write']` scored `0.4022` on
    `meeting_note_taker` vs `0.0000` on `audio_transcriber`, and both accept `audio`).
- Addressing close lexical ties between compatible candidates without per-query rules is tracked in
  [`docs/ROADMAP_AND_OPEN_QUESTIONS.md`](ROADMAP_AND_OPEN_QUESTIONS.md) under **ambiguous-match
  breakpoint**.

## What this is and is not

- It **is** a real, reproducible measurement of paraphrase generalisation under the current seed
  exemplars, run on the memory backend.
- It **is not** an assertion that routing is "correct 70%/80% of the time" for arbitrary users: the
  samples are two hand-written sets of 20 paraphrases each (now development sets) and the seed
  vocabulary is intentionally small. Across both sets (40 requests) the router produced **30 correct**
  (14 + 16); **7 of the 10 misses declined to execute** (`5 no_match` + `2 wrong-then-break`) and
  **3 executed a wrong agent** (Set 1 row 13; Set 2 rows 11 and 14).
- The full-routing unit tests (`tests/test_routing_and_runner.py`) assert the seeded, in-vocabulary
  behaviour; this file covers only the paraphrase evaluation sets.
