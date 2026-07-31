# Lesson briefs

One file per lesson, as delivered. Kept because the *format* turned out to be
the reusable part of this experiment — if this approach gets used for another
technology or another engineer, these are the thing to copy, not
`CURRICULUM.md`'s content.

Read alongside:

- [`CURRICULUM.md`](../../CURRICULUM.md) — training state, lesson plan, loop
  protocol, the hard rule. Source of truth.
- [`NOTES.md`](../../NOTES.md) § Process journal — what worked and what didn't
  about the loop itself, written as it happened.

| Lesson | Brief | Notes |
|---|---|---|
| 1 | [lesson-01.md](lesson-01.md) | Written into `CURRICULUM.md`'s lesson records; moved here unedited. Strong on concept-before-code and on docs links. |
| 2 | [lesson-02.md](lesson-02.md) | First brief written to the structure below, and the first whose scaffold was dry-run before handoff. |
| 3 | [lesson-03.md](lesson-03.md) | §5 used for a limitation of the *cluster* rather than of the API: the durability choice can't be evaluated locally. First scaffold dry-run against wrong implementations as well as a right one. |

Briefs are the *teaching* document. The matching **record** — what was decided,
what it cost, what stayed deferred — stays in `CURRICULUM.md` under
"Lesson N — record", because that is training state rather than instruction.

---

## Anatomy of a brief

The sections below are in the order they earn their keep, not the order a
textbook would use. A brief that skips 3, 4 or 8 tends to produce either a
stuck engineer or a passing test that taught nothing.

**1. The mental model** — as a *delta from what they already know*, never from
zero. Lesson 2 opened with "OpenSearch gave you one flat namespace, which is
why `IndexNames` carries a prefix" and only then introduced bucket/scope/
collection. The prior system is the scaffolding; use it and then take it away.

**2. What you write** — signatures and return shapes in a table. Precision here
is not spoon-feeding: it's the difference between a design exercise and a
guess-the-API exercise, and only one of those is worth their time.

**3. The real decision** — the single genuine design fork, both sides argued,
no recommendation. Lesson 2's was exception-driven vs. check-then-create
idempotency. If a lesson has no such fork it is a typing exercise; find one or
merge the lesson into its neighbour. Crucially the rubric then grades *whether
they reasoned*, not *which they picked*.

**4. Traps already measured** — verified facts that would otherwise cost a
debugging round, stated flatly with the evidence. This section must be visibly
separate from §3, so it is unambiguous what is given and what is theirs. Every
item here should also be in `NOTES.md`, because the next session needs it too.

**5. What you can't have** — the honest limitation, with data. Lesson 2's was
"no unindexed `COUNT(*)` is accurate, `request_plus` included," with the
measurement table. Omitting this is how you get an engineer grinding against a
wall you already know is solid. It also sets up a later lesson: the fix is
Lesson 5's index.

**6. Acceptance** — the exact command, and a statement that the tests have been
run green against a throwaway implementation. That second half matters: it
converts "your test might be wrong" into "anything still red is my code," which
is the difference between debugging and doubting.

**7. Rubric** — reviewable lines. Each one either has a test or is explicitly
flagged as a judgement call to be raised in review. A rubric item with no test
is a rubric item that will pass.

**8. Guiding questions** — genuinely open, answers not present anywhere in the
brief. These are what get discussed at review; they are also where the
misconceptions surface.

**9. Scope fence** — what *not* to touch, and which lesson owns it instead.
Cheap to write, and it prevents the most demoralising kind of wasted work.

---

## Process rules the briefs depend on

Both were learned the hard way; both are in the process journal with the
incident that produced them.

- **Verify every SDK literal by introspection before it enters a scaffold.**
  Enum values, exception names, method signatures. Recalling them produces a
  wrong test, and a wrong test costs the engineer a debugging round on someone
  else's mistake — the worst failure mode in this loop.
- **Dry-run the scaffold against a throwaway implementation before handoff.**
  Written outside the repo, never shown, deleted after. It is the only thing
  that reliably distinguishes "this test is hard" from "this test is
  impossible." Lesson 2's scaffold had an impossible assertion in it until this
  step caught it.
- **Then dry-run it against the *wrong* implementations.** One throwaway file
  per rubric line, each violating exactly one. Green-against-correct only shows
  the tests are satisfiable; red-against-wrong shows they discriminate. Lesson
  3's suite was 8/8 green and still let a read-modify-write `upsert` through —
  which is now its ninth test. Ten minutes, and it catches the tests that
  describe a requirement without testing it.
