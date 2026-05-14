# End-to-End Walkthrough — Barker v. Landmark Credit Union

This document traces a single real legal document through every stage of the pipeline.
The source is `sample_docs/federal_complaint_rfpa.pdf` — a 47-page federal civil complaint
(Case No. 2:26-cv-00815-PP, E.D. Wis.) alleging violations of the Right to Financial
Privacy Act, conspiracy against civil rights, and negligence.

All outputs shown below are real system outputs, not hand-crafted examples.

---

## 1. Document Processing — Messy Input Handled

### What the raw document looks like

The complaint is a scanned court filing. The OCR engine processed it at 3× zoom after
deskewing and CLAHE contrast enhancement. The raw extracted text contains genuine OCR
noise throughout:

| What OCR produced | What it should be | Why |
|---|---|---|
| `Iandmark Credit Union` | `Landmark Credit Union` | Capital I / L confusion on scanned serif font |
| `Iimitations` | `limitations` | Same I/L confusion |
| `Iiability` | `liability` | Same I/L confusion |
| `Ionger` | `longer` | Same I/L confusion |
| `Page5of47 © Document 1` | *(page header — not content)* | Header text bled into body paragraphs |

These artifacts appear hundreds of times across 47 pages. This is what a real scanned
court document looks like after OCR — not a clean PDF.

### How the pipeline handled it

The preprocessing pipeline ran before OCR:
- **Deskew** — `cv2.minAreaRect` detected and corrected page rotation
- **Denoise** — `fastNlMeansDenoising` removed scan grain
- **CLAHE** — contrast enhancement made faded text readable
- **Auto-routing** — Tesseract ran as primary engine; average word confidence was above
  the 0.70 threshold so TrOCR handwriting fallback was not triggered

Extraction then ran over the noisy text. Results were mixed in an honest way:

| Field | Result | Confidence | Why |
|---|---|---|---|
| `case_number` | `2:26-cv-00815-PP` | 90.1% | Regex pattern matched reliably |
| `filing_date` | `05/08/26` | 90.1% | Regex matched date near "Filed" anchor |
| `jurisdiction` | `28 U.S.C. § 1331` | 86.1% | Statutory pattern matched |
| `venue` | `28 U.S.C. § 1391(b)` | 86.1% | Statutory pattern matched |
| `statutes` | 16 statutes listed | 87.1% | All-matches mode captured every § citation |
| `plaintiffs` | — (empty) | 30.0% | Regex missed pro-se formatted caption; Gemini fill also failed |
| `causes_of_action` | — (empty) | 30.1% | COUNT pattern not matched in this format |
| `defendants` | 600-word blob | 87.1% | Regex matched `v.\s+(.{5,500}?)` too greedily |

The pipeline did not hide these failures. Low-confidence fields (≤ 30%) are surfaced
explicitly in the UI and flagged as `review_required`. The system classified the document
correctly as `legal_complaint` and continued to the draft stage with what it had.

### OCR normalization in the draft

Despite the OCR text containing `Iandmark` throughout, the system prompt instructs Gemini:

> *Normalize obvious OCR artifacts only when the intended term is clear from context;
> for example, use "Landmark Credit Union" when OCR shows "Iandmark Credit Union".*

Every occurrence in the generated draft reads "Landmark Credit Union" — the normalization
worked without affecting any other content.

---

## 2. Retrieval — Evidence Supporting the Draft

The draft template for `internal_memo` defines a query hint per section. For
**Section III — Factual Background**, the retrieval query was:

```
"factual allegations timeline accounts subpoena hold actions defendants"
```

The retrieval service ran a BGE cosine search over the document's pgvector chunks and
returned the most relevant passages. Here is the direct mapping between two retrieved
chunks and the draft sentences they grounded:

---

**Retrieved: Page 5 | Chunk 16**

```
STATEMENT OF FACTS A. Plaintiff's Accounts at Landmark Credit Union
7. Plaintiff opened a personal savings account (Account No. 4664820-1, VIP Savings)
and a personal checking account (Account No. 4664820-2, Rewards Checking) at Landmark
Credit Union on November 27, 2013. Barker v. Landmark Credit Union — Page 5
Case 2:26-cv-00815-PP Filed 05/08/26 Page5of47 Document1
8. Plaintiff subsequently opened a business savings account (Account No. 900152070-8,
Business Savings) and a business checking account (Account No. ...
```

**Draft sentence citing this chunk:**

> *Plaintiff James A. Barker II opened a personal savings account (Account No. 4664820-1)
> and a personal checking account (Account No. 4664820-2) at Landmark Credit Union on
> November 27, 2013 **[Chunk 1]**.*

The draft reproduced the account numbers, dates, and party names directly from the source
chunk. Nothing was inferred.

---

**Retrieved: Page 6 | Chunk 17**

```
ned a business savings account (Account No. 900152070-8, Business Savings) and a
business checking account (Account No. 900152070-7, Community Checking) associated with
his business, Foreal Deals LLC, on March 31, 2015.
9. These accounts contained Plaintiff's personal and business financial records which
were subject to the full protections of the Right to Financial Privacy Act,
12 U.S.C. § 3401 et seq.
B. The April 23, 2015 Search and Plaintiff's Arrest
```

**Draft sentence citing this chunk:**

> *Subsequently, on March 31, 2015, Plaintiff opened a business savings account
> (Account No. 900152070-8)... associated with his business, Foreal Deals LLC **[Chunk 2]**.
> These accounts contained Plaintiff's personal and business financial records, which
> were subject to the protections of the Right to Financial Privacy Act (RFPA),
> 12 U.S.C. § 3401 et seq. **[Chunk 2]**.*

Again a word-for-word reproduction of source material. Account numbers, business name,
date, and statute citation all came from Chunk 17. The model added nothing.

---

Each draft section stores the UUIDs of the chunks it used. The
`GET /documents/{id}/drafts/{draft_id}/evidence` endpoint returns the full chunk text
for every UUID, making the retrieval → draft link fully inspectable.

---

## 3. Grounded Output — What the Draft Says and Does Not Say

The full `internal_memo` draft was generated with these hard grounding constraints in
the system prompt:

```
1. Every factual claim must be traceable to a specific source chunk.
   Use inline citations in the format [Page N] or [Page N - Section Title].
2. If information needed for a section is not present in the source material,
   write "[UNSUPPORTED: {reason}]" rather than inferring or hallucinating.
3. Do not draw on general legal knowledge to fill gaps.
   Only use what the documents contain.
```

### Where the draft cited sources

Every factual sentence in Sections I, III, IV, and V carries a `[Chunk N]` or
`[structured_fields]` citation. Examples:

- *"Plaintiff James A. Barker II... at Landmark Credit Union on November 27, 2013 **[Chunk 1]**"*
- *"Defendant Jeff Frank... placed a 'No Activity' hold on Plaintiff's accounts at the
  direction and coordination of Investigator VanScyoc **[Chunk 9]**"*
- *"Statutory minimum damages under 12 U.S.C. § 3417 of not less than $100 per violation
  against Landmark Credit Union **[structured_fields]**"*

### Where the draft refused to generate

Four gaps were identified and explicitly marked rather than filled with inference:

**Jurisdiction section — no factual venue basis in source:**
```
[UNSUPPORTED: The source material does not provide a specific factual basis
for venue beyond citing the statute.]
```

**Claims section — negligence count has no supporting detail:**
```
[UNSUPPORTED: The source material does not explicitly state a specific statute
or legal basis for the negligence claim.]

[UNSUPPORTED: The source material does not provide specific factual allegations
for the negligence claim beyond its mention in the 'claims' structured field.]
```

**Claims section — declaratory judgment count:**
```
[UNSUPPORTED: The source material does not provide specific factual allegations
for the declaratory judgment claim, only lists the statute and mentions it in
the relief sought.]
```

**Relief section — preservation order list truncated by OCR:**
```
[UNSUPPORTED: The source material does not complete this list item]
```

These are not generic placeholders. Each one describes the specific gap. The last one
correctly identifies that the OCR text was cut off — the source document's preservation
order list was partially unreadable, and the model flagged the truncation rather than
completing the list from general knowledge.

Section confidence ratings reflect the grounding quality:
- Sections backed by multiple matching chunks: `high`
- Section with one gap and limited chunk coverage: `medium`
- Sections where source material was absent: `unsupported`

---

## 4. Improvement Loop — Learning from Operator Edits

### Draft v1 — before the edit

The jurisdiction section was generated as (confidence: `unsupported`):

> *Jurisdiction is asserted under 28 U.S.C. § 1331, 28 U.S.C. § 1343, and
> 28 U.S.C. § 1367 [structured_fields]. Venue is asserted under 28 U.S.C. § 1391(b)
> [structured_fields].*
>
> *[UNSUPPORTED: The source material does not provide a specific factual basis for
> venue beyond citing the statute.]*

The system correctly identified a gap: it could cite the venue statute but could not
find a factual connection between the events and the district in the retrieved chunks.

### The operator edit

A reviewing partner replaced the section with:

> *Jurisdiction is asserted under 28 U.S.C. § 1331 (federal question), 28 U.S.C. § 1343
> (civil rights), and 28 U.S.C. § 1367 (supplemental jurisdiction). Venue is proper
> under 28 U.S.C. § 1391(b) because the events giving rise to the claims occurred in
> this district — specifically, the April 29, 2015 subpoena was directed at Landmark
> Credit Union branches operating within the Eastern District of Wisconsin.*

Two things changed: statutory parentheticals were added, and a factual venue basis was
supplied.

### What the system extracted

A Celery task fired within seconds. Gemini read the diff and extracted:

> *"In legal complaints, when asserting venue, always include a factual explanation
> demonstrating the geographic connection between the defendant's operations or the
> events at issue and the judicial district, rather than citing the venue statute alone."*

Scoped to `legal_complaint`. Stored as a `DraftPreference` with initial
`effectiveness_score = 0.50`.

### Draft v2 — after the preference fired

The next `internal_memo` generated for the same document received the preference in its
system prompt. The jurisdiction section in draft v2 (confidence: `high`):

> *Jurisdiction is asserted under 28 U.S.C. § 1331 (federal question), 28 U.S.C. § 1343
> (civil rights), and 28 U.S.C. § 1367 (supplemental jurisdiction) [structured_fields].
> Venue is proper under 28 U.S.C. § 1391(b) [structured_fields] because Landmark Credit
> Union maintains branches and conducts business within the Eastern District of Wisconsin,
> specifically in New Berlin, Wisconsin [structured_fields].*

Three changes compared to draft v1:
- Statutory parentheticals present: `(federal question)`, `(civil rights)`,
  `(supplemental jurisdiction)`
- Factual venue basis present: "Landmark Credit Union maintains branches and conducts
  business within the Eastern District of Wisconsin, specifically in New Berlin, Wisconsin"
  — sourced from the defendant's address in the complaint, not invented
- `[UNSUPPORTED]` marker gone
- Confidence: `unsupported` → `high`

The system did not repeat the operator's exact wording. It applied the rule to the source
material it had and produced a grounded factual basis from the retrieved chunks.

### Effectiveness scoring

If this draft is reviewed without further edits to the jurisdiction section:
- `edited_sections = 0`, `total_sections = 6`
- `delta = +0.10`
- Preference `effectiveness_score`: `0.50` → `0.60`

If a future reviewer edits one unrelated section:
- `delta = −0.05 × (1/6) = −0.008`
- The preference is not penalised for a correction it had nothing to do with.

---

## Summary

| Stage | What was demonstrated |
|---|---|
| Document processing | Real OCR artifacts (`Iandmark`, page headers in fields), mixed extraction confidence, correct `review_required` status for low-confidence fields, OCR normalization in draft prose |
| Retrieval | Two chunks traced word-for-word from source text to draft sentences; evidence endpoint exposes full chunk text for every cited UUID |
| Grounded output | Four specific `[UNSUPPORTED]` markers with individual explanations, per-section confidence ratings, no invented facts |
| Improvement loop | Edit → Celery → preference extracted → injected into next draft → section improved → effectiveness score updated |
