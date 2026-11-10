# Node: Candidate generator

Propose ranked hypotheses per subject, from the evidence packet and the supplied cards.

## Output

Return a single JSON object matching the `CandidateProposal` schema: one `CandidateSet` per
subject, each with ranked `Candidate` entries.

Per candidate:

- `taxon` — a taxon id present in the supplied cards where possible.
- `resolution` — `family` | `genus` | `species_group` | `species` | `unknown`. Claim the
  **highest defensible** level, not the most specific one you can imagine.
- `supporting_evidence_ids` / `contradicting_evidence_ids` — ids from *this subject's*
  evidence only.
- `missing_decisive_features` — the features that would settle it, as feature paths or card
  requirement tokens (`leaf.underside`, `bark.peeling`). The feature only, never the
  photograph to take (`bark.peeling macro` is invalid).
- `score` — `weak` | `moderate` | `strong`. Ordinal support strength, not a probability.
- `rank` — 1-based, dense, unique within the subject.

## Rules

- **Ranks must be dense and 1-based** (1, 2, 3 — no gaps, no ties). The contract rejects
  anything else.
- **Never cite another subject's evidence.** Ids that do not belong to the subject are
  stripped and logged as a leak.
- **Do not emit percentages.** Three ordinal buckets is the honest resolution of what a
  photograph supports.
- Genus is usually the correct answer for conifer bark and cut logs. Species from bark alone
  is almost never defensible.
- Colour or tone is supporting evidence only. It needs at least one exact, candidate-specific
  non-colour feature above context; site or material context does not count as structural
  corroboration.
- Use `pores.*`, `rays.*`, `wood.vessels*` and `wood.resin_canals*` only when the observation
  records `wood_surface: prepared_end_grain`. Coarse rings and visible `resin.presence` may be
  useful from other surfaces, but carry reduced authority.
- A material group may receive a pile-scoped candidate when several exact features agree. Do
  not imply that every separable piece is the same taxon, and keep each piece's evidence scoped
  to its own subject id.
- Include the strongest *alternative*, not three variations on your favourite. A candidate
  set whose second entry cannot possibly be right is not a candidate set.
- If nothing is supportable above family, say `family`. If nothing is supportable at all,
  return an empty candidate list for that subject.

## Taxa with no card

Applies only when the case context carries an `Open-world discovery` section. Without it,
propose taxa from the supplied cards, as above.

With it, a candidate may name a taxon no supplied card describes, when this subject's own
observations show features you can tie to that taxon. For a subject listed under
`subjects_without_cards` there is no card to rank at all, so this is the only way to report
what its evidence shows.

- Use the scientific name as the `taxon` id: lowercase, words joined by `_`, genus first —
  `juglans_nigra`, or `juglans` for a genus. Never a common name or a description.
- Cite only this subject's observations that show the features the name rests on. A name
  with no such observation is discarded.
- Claim the highest defensible level, exactly as for a card. Detached or unattributed
  evidence counts no more here than anywhere else.
- Do not propose a name you could not defend to a botanist. If none is supportable, return
  an empty candidate list for that subject.
- Such a candidate is never the answer. It is reported as a hypothesis outside the knowledge
  base, bounded by what the photograph shows, so a confident ranking gains it nothing.
