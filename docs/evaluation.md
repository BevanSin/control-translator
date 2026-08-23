# Measuring mapping quality

The pipeline can be verified for *correctness* by the test suite, but correctness
says nothing about whether the mappings it produces are any *good*. This harness
scores mapping output against a published reference so changes to retrieval, the
classifier, or the prompt can be judged on evidence instead of intuition.

## The reference set

`data/source/nzism-v3.8.json` is Microsoft's published NZISM regulatory-compliance
initiative. Each `policyDefinitionGroup` is named `New_Zealand_ISM_<control_id>`
and each policy definition lists the groups it belongs to, which yields control to
policy pairs directly. Extraction is shared with `ct seed` via
`seeds.extract_initiative_pairs`, so both read published initiatives the same way.

At the time of writing that gives 32 controls and 208 pairs.

## This is a reference, not truth

The initiative reflects one publisher's judgement at one framework version. Our
tool may legitimately propose policies Microsoft did not, and NZISM 3.9 is not
3.8. Treat the score as a **regression baseline and a way to compare two
configurations**, never as a grade. Optimising toward 100% against it would make
the product worse by teaching it to imitate a specific historical answer.

Two kinds of drift are therefore reported separately rather than counted as
failures:

- **controls absent from the framework** — 5 of the 32 were renumbered or
  withdrawn between 3.8 and 3.9
- **policies absent from the catalogue** — 2 of the 208

Reporting both means a score is always read next to the ceiling it could have
reached.

## Running it

```
ct eval --config config/nzism-eval.json --gold data/source/nzism-v3.8.json
```

`config/nzism-eval.json` is deliberately offline: the bundled catalogue, the
heuristic classifier, and TF-IDF retrieval. It needs no credentials and no
network, so the retrieval numbers are reproducible anywhere.

Useful options:

| Option | Purpose |
| --- | --- |
| `--at-k 5,12,25,50` | shortlist sizes to report `recall@k` for |
| `--limit N` | evaluate only the first N controls, for quick smoke runs |
| `--output PATH` | write the full JSON report, including a per-control breakdown |
| `--group-prefix` | strip a known group name prefix when recovering control ids |

To compare configurations, run it twice with different `mapping` settings and
compare the reports.

## What the metrics mean

**`recall@k` is the important one.** It asks whether the retriever's shortlist
even contains the gold policy. A policy that is never shortlisted can never be
selected by any classifier, however good, so this is the hard ceiling on the
entire product. It involves no model, so it is deterministic and free. It is also
the number that makes `top_k` a tunable decision rather than a guess.

**Selection precision, recall, and F1** describe what the classifier did with the
shortlist it was given. These only mean something next to the name of the
classifier that produced them, which is why the report always states it. Note
that the offline `heuristic` classifier marks a policy relevant on *any* token
overlap, so it accepts nearly everything it is shown; its precision is close to
meaningless and its recall simply tracks `recall@k`. Use it as a fixed reference
point when measuring retrieval, not as a quality target.

**Previously rejected policies** counts proposals that a human already rejected
in the OOS/ignore register. A reviewer rejecting a policy is a statement about
the policy itself, so re-proposing it is worth watching independently of any
published mapping.

## Baseline finding

The first run against the full reference set, offline with TF-IDF at the
production `top_k` of 12, recovered **7.6%** of the reference pairs. Widening the
shortlist to 200 candidates only reached 33.7%.

Inspecting a single control shows why. For `06.2.5.C.01` ("Vulnerability
Analysis") the three gold policies are all literally vulnerability-assessment
policies, yet TF-IDF ranks them 112th, 184th, and 208th out of 2,312. Control
prose is governance language — "agencies", "should", "system", "significant
change" — while policy descriptions are technical Azure language, so the shared
vocabulary is thin and the distinctive term is diluted.

Weighting the control title, or querying on the title alone, does not help; both
were measured and neither improved on the current query. So this is a genuine
limitation of lexical matching for this task rather than a tuning oversight.

Two consequences worth holding onto:

- The production NZISM config already uses `"retrieval": "embedding"`, so this
  figure describes the **offline** path, not what a configured cloud run does.
- It does mean the fully local experience is currently weak at matching even
  though the bundled catalogue made it complete. That is measurable evidence for
  keeping hybrid the default, and a concrete target for any future offline
  retrieval work.
