# Exact SOILIE-3D V4 machine audit

Run date: 2026-09-13
Model: SOILIE-3D V4 24.07.05 at `b8c7c5a2f88f5c87e499c4f05e7aa8b6f1b7c51b`
Design: 32 seeded scenes through the original V4 selection and `calculateCoords` path.

| Metric | Result | Meaning |
| --- | ---: | --- |
| Coordinate-generation success | 90.6% | Returned finite V4 coordinates. |
| Five-metre retry-boundary pass | 100.0% | Successful samples met V4's own retry condition. |
| Input-label preservation | 75.9% | Exact selected labels remained; a V4 window/blind/curtain rewrite counts as a change. |
| Relation preservation after window normalization | 96.6% | Treats V4's window/blind/curtain substitution as one architectural role. |
| Requested-count match | 56.2% | Duplicate removal did not reduce the requested count. |
| Raw scenes with a sphere overlap | 100.0% | Pre-Blender diagnostic, before V4's own stacking/separation pass. |
| Exact seeded replay | 100.0% | Repeated runs returned byte-equivalent data. |
| Median coordinate time | 2.27 s | V4 selection excluded; render time excluded. |
| p95 coordinate time | 8.53 s | Slowest 5% threshold in this fixed sample. |

The room-sizing wrapper is excluded. The recency table in the JSON records V4's literal alpha schedule and its discrepancy with a strict five-visible-object claim. Published LayoutGPT, GRAINS, and Infinigen Indoors metrics remain separate because their datasets and tasks are not matched.
