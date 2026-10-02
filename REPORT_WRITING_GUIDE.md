# REPORT_WRITING_GUIDE.md

How to update the internship report so that it describes the project as it actually is
today, and not as it was several milestones ago.

This file has two audiences. The first is me, when I plan the writing work. The second is
the AI assistant that drafts the prose, which is why Part 1 is written as a brief that can
be pasted directly into a prompt.

The source of truth for every technical claim is `handoff.md`. If this guide and
`handoff.md` disagree, `handoff.md` wins, and this file should be corrected.

---

## Part 1. The writing brief

Paste this section, unchanged, into the prompt of whichever assistant drafts a chapter.

> **Language level.** Write at B2 level. Clear, direct sentences. Avoid rare vocabulary
> and avoid long subordinate chains. A reader whose English is good but not native should
> never have to read a sentence twice.
>
> **Punctuation.** Never use the double hyphen or the em dash. Use commas instead. If a
> sentence needs a stronger break than a comma, split it into two sentences.
>
> **Voice.** Write as a monologue, as if I am speaking to the reader. The report already
> uses "we" in the chapters, so keep "we" and stay consistent with it. The tone should be
> that of someone explaining their own work out loud, calm and personal, not that of a
> specification document. Say "we noticed", "we therefore decided", "this surprised us",
> where that is what actually happened.
>
> **Cross references.** When you mention something explained elsewhere, say so explicitly,
> using the section or figure number. Write "as we have seen in Section 2.4.4", "we will
> come back to this in Section 4.2.2", "as shown in Figure 3.4", "the pipeline presented
> in Figure 2.7". Never refer to an idea as already known without pointing to where it was
> introduced.
>
> **Paragraphs, not lists.** Prefer continuous prose. A bulleted list is acceptable only
> when the items are genuinely parallel and short, for example a list of service names, and
> only once in a while. Never place two lists close together, and never use a list where
> three sentences would do. Lists make a report feel generated. Paragraphs make it feel
> written.
>
> **Figures and tables.** Leave a marked placeholder wherever a figure or a table would
> genuinely help, using the form given in Part 4 of the writing guide. Do not invent data
> for a table. If a number is needed and is not supplied in the brief, write
> `[TO MEASURE]` rather than guessing.
>
> **Honesty.** This project's strongest material is the places where a first attempt was
> wrong and measurement corrected it. Write those plainly. Never claim a result that was
> not measured, and when something was estimated rather than measured, say which it was.

---

## Part 2. What changed since the current report was written

The report currently describes four backend services. The platform now runs eight
containers, and several items the report lists as future work have been built. This part
lists the differences, so the writer knows what is new before touching any chapter.

### 2.1 The architecture gained three components

**Redis and a separate ingestion worker.** Resume parsing used to happen inside the upload
request, which held one HTTP worker for roughly ten seconds per CV. Ingestion now goes
onto a queue. `POST /v1/resumes/jobs` returns `202` with a job id, an ARQ worker consumes
the queue, and durable job state lives in `resume_intel.ingest_jobs` so that a flushed
Redis costs throughput and never a candidate record. Redis is a broker and cache only, it
is never a source of truth, and it never holds personal data.

**Ollama as a local AI provider.** The report lists a local fallback as future work in
Section 4.5.3. It exists. It is a Compose service with GPU access, it serves four services,
and it is reached at `ollama:11434`.

**A rebuilt matching gate.** Required skills are still a hard eligibility condition, which
is what the report says, but the condition is now a coverage floor rather than an all or
nothing check, and the floor is versioned.

### 2.2 Items the report calls future work, which are now implemented

| Report section | What it says | What is true now |
|---|---|---|
| 4.5.3 Local AI Fallback | future direction | built and verified, milestones 46, 61, 65 |
| 3.4.4 Recruitment Notifications | ICS is a possible extension | ICS invitations are implemented and tested |
| 4.5.5 Large-Scale Evaluation | should be evaluated | throughput and latency measured, milestones 54, 63, 64 |

### 2.3 Statements in the report that are now incorrect

These are not additions, they are corrections. Leaving them would mean defending claims
that the code no longer supports.

**Section 2.5.3 and 3.2.3, the hard gate.** The report says every required skill must be
present. Since migration 0011 the gate passes when the fraction of required skills the
candidate holds reaches `min_required_coverage`, which is stored on the versioned scoring
configuration. Version 1 keeps the strict value of 1.0, version 2 uses 0.8.

**Section 3.1.1, resume ingestion.** The report describes a synchronous upload. Ingestion
is asynchronous and queued, as described in Part 2.1 above.

**Section 3.3.4, language evaluation.** The report describes `insufficient_evidence` as the
single outcome when evidence is thin. There are now two deterministic halves and a set of
reason codes, and the deterministic half measures the candidate's language rather than the
duration of the session.

**Section 4.4.3, technical results.** The report claims reproducibility and modularity but
gives no numbers. Numbers now exist and should replace the general statements.

### 2.4 New material that has no place in the current structure

The benchmarking work is substantial and is completely absent. Seven local models were
measured against this project's own prompts and validators, not against a public
leaderboard. The ingestion queue was load tested. The matching pipeline was timed. A
planning estimate was found to be wrong by a factor of about seventeen. None of this fits
in the existing chapters without a new section, which Part 3 proposes.

---

## Part 3. Step by step, chapter by chapter

Work in this order. Each step says what to change, and what the writer needs to know in
order to write it correctly.

### Step 1. Rewrite the architecture overview, Chapter 2, Section 2.1

Change the sentence that says the architecture is built around four main backend services.
Describe the platform as four business services, supported by a queue, a worker and a local
model provider. Keep the explanation of why responsibilities were separated, which is still
correct and well written, and add that two of the new components exist for reasons that
were discovered rather than planned. The queue exists because resume parsing was holding
an HTTP worker for about ten seconds per CV, and the local provider exists because an
external provider's quota turned out to be a real and repeated constraint.

Figure 2.1 must be redrawn. See Part 4, figure F1.

### Step 2. Add two service subsections, Chapter 2, Section 2.2

Add a subsection for the ingestion worker and a subsection for the local AI provider,
placed after the four existing ones. For the worker, explain the division clearly, the API
accepts and enqueues, the worker parses and stores, and the job table is what makes the
state durable. Mention that the worker runs with a bounded concurrency, and that raising
it was tried and measured and made throughput worse, which is in `handoff.md` milestone 54.
That is a good detail, because it shows a decision taken from measurement rather than from
intuition.

For the local provider, the important architectural point is that it is a fallback and not
a second primary. It is used only when the main provider is unavailable, and it is never
used for embeddings, because a different embedding model would write vectors into a space
that is not comparable with the stored ones.

### Step 3. Update the data architecture, Chapter 2, Section 2.3

In Section 2.3.3 on vector storage, add embedding space versioning. The stored vector now
carries the model version that produced it, and a retrieval query must match that version.
Explain the trap plainly, because it is a good example of a silent failure, the local
embedding model produces vectors of exactly the same dimension as the current one, so a
dimension check would pass and retrieval would quietly degrade instead of failing.

In Section 2.3.5 on immutable results, add that the scoring configuration is versioned and
that every match result stores the version that produced it. This now includes the gate
threshold, not only the weights, which is the reason the new threshold was added as a
column and not as a constant in the code.

Update Table 2.1 and Table 2.2 to include Redis, ARQ and Ollama.

### Step 4. Correct the matching description, Chapter 2, Section 2.5.3

Keep the final score formula, it is unchanged. Replace the description of the gate. The
qualification rule is still

```
Qualified = HardGatePassed AND FinalScore >= Threshold
```

but `HardGatePassed` is now a coverage test. Explain why the change was made, and be
concrete, because the concrete case is convincing. A consultant with fifteen years of
experience, holding Power BI, SQL Server, SSAS, SSIS and Azure Synapse, was rejected
because the brief asked for SQL and the matching was a literal string comparison. One
unmatched token outranked a decade of adjacent work. Say clearly that the change reduces
that failure but does not remove it, and that the remaining part is a vocabulary problem
which Section 4.5 lists as future work.

### Step 5. Rewrite resume ingestion, Chapter 3, Section 3.1.1

Describe the queued flow. The recruiter uploads, the API validates and stores the file and
returns a job identifier immediately, the worker performs extraction and structuring, and
the client can poll the job. Explain the reasoning in one short paragraph, the target the
company has in mind is large, and a design that occupies an HTTP worker for the duration of
an AI call cannot reach it.

Figure 3.1 must be redrawn. See Part 4, figure F2.

### Step 6. Add the required skills cap, Chapter 3, Section 3.2.1 and 3.2.2

The extraction is now asked for at most six genuine must have skills, and the validation
layer enforces that deterministically by moving anything beyond the sixth into the
preferred list. Explain why, and use the measured case. One real job description mentioned
eighteen technologies, the extraction marked all eighteen as required, the best candidate
in a pool of seventy matched four of them, and so every retrieved candidate failed the gate
on every such job. A gate that nobody can pass is not a gate, it is an unconditional
rejection.

Be precise about one point, because a careful reader will ask. Moving a skill from required
to preferred does not change the score at all, since the skill score counts the union of
the two lists. It changes only what is allowed to disqualify.

### Step 7. Rewrite the sufficiency rules, Chapter 3, Section 3.3.4

The deterministic half of the decision used to be the duration of the session and the
number of candidate turns. It is now a measurement of the candidate's language, the number
of words they produced, the number of turns long enough to count as a real answer, and the
number of distinct words. Duration survives only as a floor that catches a session too
short to be an interview at all.

The reason for the change is worth telling as a short story, because it is a clean example
of a proxy failing. The richest real interview in the database, four hundred and
eighty five candidate words across fifteen substantial turns, was discarded for being
sixteen seconds under a five minute threshold, while a long session in which the candidate
said very little would have passed.

Then explain the part that makes the design defensible. Measuring the golden cases showed
that no word count can separate every case, because there are two different kinds of
insufficiency. One is a shortage of language, which can be counted. The other is speech
that is fluent but does not answer the question, which cannot. The system keeps a
deterministic measure for the first and the model's own judgement for the second, and
records which of the two produced the decision.

### Step 8. Add a section on the local AI fallback, Chapter 3

This is new material and it deserves its own section, placed after VoiceHR. Suggested
number, 3.6, with the administration interface moving to 3.7.

Cover four things. First, that seven candidate models were benchmarked against this
project's own prompts, schemas and validators rather than against a public leaderboard,
and that different tasks selected different winners, which is why the configuration names a
model per task instead of one model everywhere. Second, that the evaluation fallback is
deliberately advisory, it produces dimension scores and no CEFR level, because the chosen
model compresses the scale and the language gate would otherwise accept a weaker candidate
during an outage. Third, that the provider is a Compose service, and why, which is the
story in milestone 65, the fallback failed silently in four different ways while every
container reported healthy. Fourth, that quota turned out to be enforced per model, so
putting evaluation on a different model from extraction spreads the load across separate
quota buckets.

### Step 9. Correct the notifications section, Chapter 3, Section 3.4.4

Replace the sentence that calls ICS a possible extension. Describe what exists, scheduling,
rescheduling and cancellation carried in a calendar compatible invitation, and say honestly
what has not been confirmed, which is the visual rendering in a real calendar client.

### Step 10. Update the testing chapter, Chapter 4, Sections 4.1 and 4.2

Give the real numbers. Eight suites, eight hundred and thirty six tests. Mention that the
suites are run by a single command, and that continuous integration runs them on every
push, which Section 4.1 currently does not mention at all because it did not exist.

Add one subsection that is, in my view, the most valuable thing in this chapter. The test
suites run on the host, against the repository, and therefore cannot see breakage that
exists only inside a container image. This was not theoretical, it happened three times,
and in one case a shared schema change was missing from a running service for two
milestones while every test passed. State the lesson in one sentence, a test that passes
on the host says nothing about the image that is deployed.

### Step 11. Add a benchmarking section, Chapter 4

New material, suggested number 4.3, with the existing reliability section moving to 4.4.

Three measurements belong here. The local model benchmark, which selected a different
winner per task. The ingestion load test, which produced a throughput figure and, more
interestingly, showed that the number alone is meaningless without knowing which provider
served the work. And the matching latency measurement, which found the submission time flat
with respect to the number of retrieved candidates, and which corrected a planning estimate
that was wrong by a factor of about seventeen.

That last one should be written honestly and without embarrassment. The planning document
estimated a cross service round trip at fifty milliseconds and concluded that the N plus
one query was the sharpest bottleneck in the system. Measured, the round trip is about
three milliseconds, because both services sit on the same Docker network. The work was
still worth doing, but the priority given to it rested on a number that nobody had
measured. That is a genuine lesson about estimation and it reads much better than a
paragraph claiming everything went to plan.

### Step 12. Rewrite results and limitations, Chapter 4, Sections 4.4 and 4.5

Replace the general statements in the technical results with the measured ones. Then update
the limitations honestly. The real current limitations are the external provider quota,
which is enforced per model and was hit repeatedly, the matching vocabulary, which still
compares skill names literally, the sufficiency thresholds, which are provisional because
they were calibrated on golden cases rather than on real interviews, and the small number
of real interview transcripts available for calibration.

Finally, rewrite Section 4.5. The local AI fallback moves out of future work, since it is
built. The new future work is the skill vocabulary, a normalisation and subsumption table,
possibly seeded from a public multilingual occupation taxonomy, which would let SQL Server
satisfy a requirement for SQL without giving up a deterministic and explainable match.

### Step 13. Fill the empty front and back matter

The abstract, the French résumé, the list of acronyms, the general introduction and the
bibliography are all empty in the current document. Write them last, when the chapters are
settled, because the abstract should describe what the report actually ends up saying.

---

## Part 4. Figure and table placeholders

Use this exact form in the text, so the places are easy to find later.

```
[FIGURE F3: short description of what the figure must show]
```

Figures that the updated report needs. F1 and F2 replace existing figures, the rest are
new.

**F1, overall architecture, replaces Figure 2.1.** Must show the four business services,
Redis and the ingestion worker, the Ollama provider, the shared PostgreSQL with its three
schemas, and VoiceHR's separate SQLite. The frontend must be shown talking to one service
only.

**F2, queued resume ingestion, replaces Figure 3.1.** Upload, immediate job id, queue,
worker, extraction, candidate record, knowledge record. The point of the figure is that the
recruiter is not waiting for the AI call.

**F3, the provider fallback path.** Primary provider, availability failure, local provider,
and the two outcomes, a normal result for extraction and structuring, and an advisory
result with no CEFR level for evaluation.

**F4, the sufficiency decision.** The two halves, the countable one and the judgement one,
and the four reason codes that can come out.

**F5, the graded skills gate.** Required skills, coverage fraction, the floor from the
versioned configuration, and the outcome, with the missing skills still displayed to the
recruiter even when the gate passes.

Tables the updated report needs.

**T1, benchmark results per task.** One row per task, the selected model, the measured
quality figure, and one sentence of justification. Data is in
`LOCAL_LLM_BENCHMARK_AND_FALLBACK.md` and in `handoff.md` milestones 46 and 61.

**T2, measured performance.** Ingestion throughput, submission latency against several
values of the retrieved candidate count, and the fetch comparison before and after
batching. Data is in `handoff.md` milestones 54, 63 and 64, and in
`benchmarks/load/results/`.

**T3, test suites.** One row per suite, the command, and the expected count. Data is in
`TESTING.md`, which is already accurate and can be copied.

**T4, updated technology table, replaces Table 2.2.** Add Redis, ARQ and Ollama.

---

## Part 5. Suggested updated table of contents

Only the parts that change are listed. Everything not mentioned keeps its current number
and title.

```
2.2.6  Resume Ingestion Worker                      new
2.2.7  Local AI Provider                            new
2.3.3  Vector Storage and Embedding Versioning      retitled, extended
3.1.1  Queued Resume Ingestion                      retitled, rewritten
3.2.3  Deterministic Matching and the Graded Gate   retitled, rewritten
3.3.4  Language Evaluation and Sufficiency          retitled, rewritten
3.6    Local AI Fallback                            new section
3.7    Administration Interface                     renumbered from 3.5
4.1.6  Container Level Verification                 new
4.3    Benchmarking and Measurement                 new section
4.4    Reliability and Security Validation          renumbered from 4.3
4.5    Results and Discussion                       renumbered from 4.4
4.6    Deployment and Future Improvements           renumbered from 4.5
```

---

## Part 6. Facts the writer may need, with their sources

Every number below is in `handoff.md` or in a results file. Do not write a number that is
not in this list without checking.

| Fact | Value | Source |
|---|---|---|
| Containers in the stack | 8 | `infra/docker-compose.yml` |
| Automated tests | 836 across 8 suites | `TESTING.md` |
| Models benchmarked locally | 7 | milestone 46 |
| Ingestion throughput, local fallback path | about 670 to 900 CVs per hour | milestones 54, 63 |
| Submission latency against top_k | flat, about minus 0.3 ms per extra candidate | milestone 64 |
| Cross service round trip, estimated | 50 ms | `SCALING_AND_FUTURE_IMPROVEMENTS.md` section 4 |
| Cross service round trip, measured | about 3.0 ms | milestone 64 |
| Batch fetch speed up at 40 candidates | 12.2 times | milestone 64 |
| Required skills cap | 6 | `jd_validation.MAX_REQUIRED_SKILLS` |
| Gate coverage floor, scoring config v2 | 0.8 | migration 0011 |
| Sufficiency floor | 30 candidate words, 3 substantial turns, 8 distinct words | `sufficiency.py` |
| Real transcripts available for calibration | 2 substantial, out of 16 | milestone 66 and the survey |

---

## Part 7. Things to avoid writing

The report should not claim that the platform was validated at scale, because it was not.
It should not present the sufficiency thresholds as measured, because they were chosen
inside a gap that the golden cases leave and then documented as provisional. It should not
describe the local models as equivalent to the external provider, because the benchmark
showed the opposite for the evaluation task, which is exactly why that fallback is
advisory. And it should not quote a throughput number without saying which provider served
the work, because the first time that was done the number turned out to describe the
fallback rather than the system.
