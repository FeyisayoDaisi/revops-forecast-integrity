# Stage 3: Weekly forecast review pack

**Problem.** Forecast calls turn into opinion contests, and someone spends a day a week building the report by hand.

**Approach.** One script builds the Monday pack from this week's and last week's snapshots, the Stage 1 flags and the Stage 2 forecasts: the number with a range, gap to target and coverage; a waterfall of pipeline movement (new, pulled in, pushed out, won, lost, amount changes); deals at risk with their reason; reps whose call disagrees with the model; and a one-page CRO memo. The script computes every number. The AI only writes the memo narrative from those numbers. Reconciliation checks then fail the pack if the memo contains any figure that is not in the computed facts, or if the waterfall does not add up.

**Results (Q4 2026, week 5, synthetic data).**
- Recommended forecast $559,000 against a $590,000 target, range $433,000 to $685,000. The rep call on the raw CRM is $355,400.
- 9 in-quarter deals worth $412,200 flagged at risk. 5 reps flagged for a rep-versus-model gap, including one rep calling $0 where the model sees $113,200.
- Reconciliation 5 of 5 pass: all 17 memo figures match the computed facts, and the waterfall adds up from $2,490,400 to $2,383,600. Memo rubric 3 of 3.
- The whole pack builds in seconds.

**Trade-offs.** The memo rubric's "explains the change" and "names actions" tests are simple and transparent rather than clever. A person still reads the memo before it goes out.

**Real work this mirrors.** Automating executive reporting for a Plumemark Digitals client cut manual reporting by 78%, from about a full day a week to under 30 minutes.

**Production next steps.** Send the pack to Slack or email every Monday, and add last week's forecast versus this week's to show how the call is moving.
