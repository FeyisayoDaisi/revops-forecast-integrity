# Stage 1: Pipeline definition and hygiene

**Problem.** A forecast is only as good as the records under it. When stages mean different things to different reps, dead deals stay open and close dates slip without consequence, the forecast is wrong before anyone does any maths.

**Approach.** I wrote one page of stage definitions with exit criteria, required fields, time limits and win probabilities, and put the same rules in a YAML file the code reads. Every Monday a rules check scores each open deal against them: missing exit criteria, stale activity, past-due close dates, three or more pushes, commit without a next step or decision maker, missing data and duplicates. Each flagged deal gets a health score and one proposed fix. One AI call per deal with a process flag explains why it is flagged (abandoned, mis-staged, stalled or a false alarm) and drafts a nudge to the owner. A person approves every fix before anything changes.

**Results (synthetic data with a planted answer key, five audit dates, 316 deal reviews).**
- Deal-level precision 98% and recall 97%: 126 problem deals caught, 3 false alarms, 4 missed.
- The AI verdict matched the answer key on 85.6% of 104 flagged deals, and every "abandoned" call was right.
- On the current pipeline, approved fixes took commit from $244,500 to $73,500 and weighted Q4 pipeline from $629,560 to $541,110.

**Trade-offs.** The 45-day stale rule misses deals that died recently (16 misses). A shorter threshold would catch them and flag more healthy deals. Exact duplicate matching misses retyped amounts (3 misses). Ticked-but-untrue qualification boxes cannot be caught by any field rule.

**Real work this mirrors.** For a Plumemark Digitals client I scored open deals against stage exit criteria and data-completeness rules, then removed or re-staged the ones that failed: $2.1M of phantom pipeline removed and forecast variance cut from 34% to under 10%.

**Production next steps.** Monday snapshots from HubSpot or Salesforce, approved nudges posted to Slack, fuzzy duplicate matching.
