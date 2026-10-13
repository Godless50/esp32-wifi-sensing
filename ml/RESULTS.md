# Motion model experiments (2026-10-13, night data)

Data: 8 sessions, ~36 min (20 min static with spoken occupancy labels + 16 min phone walks).
Test: last 25 % of each session, 2 s gap, real windows only (817 windows, 81 moving).
Metric: balanced accuracy (bal), plus TP/FP on the test set.

| model | train data | bal | TP/81 | FP/736 | size |
|---|---|---|---|---|---|
| std threshold (fit on real train) | real | 0.861 | 81 | 204 | — |
| std threshold, tuned on test (oracle, optimistic) | — | 0.907 | 74 | 74 | — |
| CNN v0 (fixed windows) | real 3.6k | 0.783* | — | — | 14.6 KB |
| CNN v1 | 40k: real + channel augmentation | 0.862 (int8 0.794) | 72 | 121 | 14.6 KB |
| CNN v1 | 40k: + 35 % synthetic composition | 0.679 | 72 | 390 | 14.6 KB |
| MLP on variation features | 40k: augmentation only | 0.845 | 63 | 64 | 5.3 KB |
| MLP on variation features | 40k: + 35 % synthetic composition | 0.863 (int8 0.862) | 66 | 65 | 5.3 KB |

*v0 used a different split granularity (fixed 0.5 s windows incl. label edges).

Findings:
- Augmentation (time-stretch, gains, noise, packet loss) fixes CNN overfitting (0.78 → 0.86).
- Synthetic composition (moving pattern × other still background) hurts the CNN (domain shift)
  and gives a marginal gain for the feature MLP.
- No model beats a well-tuned variance threshold: the ceiling is set by label noise
  (spoken minute-level labels; "still" segments contain movement) and by one radio link.
- Next: scripted phone walk (exact labels, zones) and multi-link CSI (ping-elicited transmitters).
