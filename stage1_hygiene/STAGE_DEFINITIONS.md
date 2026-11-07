# Pipeline stage definitions

One page for the sales team. A deal moves to the next stage only when it meets the exit criteria of the stage it is in. The rules check in this repo reads the same definitions from `data/stage_rules.yaml`, so this page and the code always match.

| Stage | What it means | Exit criteria (what must be true to move on) | Must be filled in | Time limit | Win probability |
| --- | --- | --- | --- | --- | --- |
| **1. Discovery** | We have a named contact with a real problem and a booked next meeting. | Next step booked with a date. | Next step | 30 days | 10% |
| **2. Qualification** | The buyer has confirmed the pain in their own words, and it is worth solving this year. | Pain confirmed by the buyer and recorded. | Next step, pain confirmed | 30 days | 20% |
| **3. Solution** | We have shown the solution to the person who signs, or to someone who has confirmed they will take it to them. | Decision maker identified by name. | + decision maker identified | 35 days | 40% |
| **4. Proposal** | A priced proposal is in the buyer's hands and budget is confirmed. | Budget confirmed against the proposal amount. | + budget confirmed | 35 days | 60% |
| **5. Negotiation** | Terms are being agreed and we know the buyer's signing process and date. | Paper process (legal, procurement, signer) known and dated. | + paper process known | 30 days | 80% |

## Forecast categories

- **Commit:** I would bet my number on it. Negotiation stage, a dated next step, the decision maker named, closing this quarter.
- **Best case:** It can close this quarter if things go right. Usually Proposal.
- **Pipeline:** Real, but not this quarter's number yet.

## Hygiene rules (checked every Monday)

| Rule | A deal fails when |
| --- | --- |
| Exit criteria | Any required field for its current stage is empty |
| Stale | No buyer activity for 45 days or more, in Solution or later |
| Past-due close date | The close date is in the past and the deal is still open |
| Serial slipper | The close date has been pushed later 3 or more times |
| Happy ears | It is in commit without a next step or without a named decision maker |
| Missing data | Amount or close date is blank |
| Duplicate | Same account and same amount as an earlier record |
| Over time limit | Longer in the stage than its time limit (lowers the health score, not a flag on its own) |

**What happens to a flagged deal:** the owner gets one proposed fix and a short note. Nothing changes in the CRM until a RevOps lead approves the fix.
