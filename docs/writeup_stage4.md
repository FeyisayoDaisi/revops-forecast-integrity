# Stage 4: Deal desk

**Problem.** Discounts creep up at quarter end, approvals happen after the contract is signed, and promises made in an email never reach Legal. The forecast counts the revenue, but nobody knows how much margin was given away to get it, or which deals closed on terms the business never agreed to.

**Approach.** I wrote the pricing and approval policy as one YAML file: who can approve each discount band, payment terms, free months, contract length and non-standard clauses, with a hard ceiling nobody can approve past. A check runs every quote against it. It accepts a more senior sales approver in place of a junior one, flags clauses promised in free-text notes, approvals dated after close, quote maths that does not match the CRM amount, and deals signed on an expired quote. Open quotes go to an approval queue with the approver and their SLA. The same quotes feed discount analytics by rep, by week of quarter and by discount band.

**Results (synthetic data with a planted answer key, 162 quotes).**
- 54 of 56 planted policy breaks caught (96%) with no false alarms at quote level.
- Discounts rose from 14.5% to 19.1% in the last two weeks of the quarter, and win rate stayed flat across discount bands, so the extra discount bought nothing.
- $1,077,830 of list price given away on $4,208,500 of won revenue, and 24 won deals worth $872,800 closed on terms never properly approved.
- This quarter, one in-forecast deal worth $140,000 waits on Finance.

**Trade-offs.** Keyword rules miss promises written in plain language ("they can walk away at month 6"), which caused 2 of the misses. That is where an AI reader, checked by a person, would earn its place. The rep flag needs 5 won deals before it judges anyone, so one planted discounter sits just under the line.

**Real work this mirrors.** I have hands-on CPQ experience in PandaDoc and Upsert.

**Production next steps.** Run the check when a quote is created in the CRM instead of after the fact, and post approval requests to Slack with the SLA clock.
