# Stage 2: Forecast methods and back-test

**Problem.** Leadership gets one forecast number with no idea how reliable it is or why it moved.

**Approach.** I built three forecast methods and ran each on the raw CRM and on the pipeline after the Stage 1 rules. Rep call is commit plus half of best case. Stage-weighted multiplies each amount by its stage probability. Historical conversion learns, from earlier quarters only, how much of each stage really closed, and adds the business earlier quarters won from deals not yet in the pipeline. The back-test forecasts four past quarters at weeks 2, 6 and 10 using only the snapshot available that day, then compares with what closed. A leakage check logs every data access and confirms nothing came from after the forecast date (624 checks, 0 violations).

**Results (synthetic data, 12 forecasts per method).**
- Cleaning first cut error for every method: stage-weighted from 45.3% to 24.6%, rep call from 41.6% to 33.2%, historical conversion from 27.5% to 21.0%.
- The best method on clean data has almost no bias (-0.1%), against +43.0% for stage-weighted on raw data.
- The rep-bias table caught both planted sandbaggers and classified 12 of 15 reps correctly.

**Trade-offs.** Cleaning removes inflated commit but cannot add sandbagged deals, so the rep call on clean data under-forecasts by about 21%. That is why the review pack puts the rep call and the model call side by side. With 13 to 18 won deals per quarter, one large deal moves the error a lot, so the figures are directional.

**Real work this mirrors.** Forecast variance cut from 34% to under 10% after the pipeline cleanup for a Plumemark Digitals client.

**Production next steps.** Segment-level conversion rates, and a tracker that scores last quarter's call against what closed.
