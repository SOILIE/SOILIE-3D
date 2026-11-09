# SOILIE-3D tests

Run the core regressions and existing serverless tests together from the project
root:

```sh
python -m tests
```

The tests use Python's standard-library unittest runner. Install the numerical,
image, and service-client dependencies from
[`serverless/requirements-ci.txt`](../serverless/requirements-ci.txt). Service
operations use fake clients, and the runner blocks network connections in its
Python process. Tests do not purchase model calls or generate a benchmark cohort.

## Full generation gate

With the model's data, registered assets, compiled runtime, and Blender available:

```sh
python -m tests --all --blender /path/to/blender --report .codex/tests/full-result.json
npm run test:version
```

Use the same Blender version as the frozen generation runtime (currently
3.6.23). The full gate requires the compiled runtime database and runs real
Blender fixtures plus an import check of every selectable OBJ. It refuses to
pass missing-data or dependency skips. OS-specific console and process tests
run on the matching operating system; the JSON report identifies skips so both
Windows and Linux results can be retained when both are used.

`SOILIE_RUNTIME_DB` can select a compiled index outside the default
`.codex/runtime/relations.sqlite3`. `SOILIE_BLENDER` can supply the executable
instead of `--blender`. Follow the [runtime instructions](../serverless/README.md)
to prepare a fresh checkout; preserve metadata already bound to a campaign.

All temporary fixtures and optional reports stay under `.codex/`. The runner
cleans its scratch directory and uses the shared timing lock for Blender work.

## Coverage

- Source geometry: depth encodings, camera-to-world transforms, pixel origins,
  frame pairing, occlusion, annotation instance identity, and finite inputs.
- Data preparation: ordered angles, frame-specific relation provenance,
  triplet aggregation, atomic rollback, rigid alignment, seeded base selection,
  and coordinate construction for three through nine selected instances.
- Catalogs and requests: three through eight counted non-window instances,
  optional window extras, exact explicit lists, duplicate policy, nine-column
  catalogs, and unsupported-combination rejection without fallback sampling.
- Renderer: actual imported assets, deterministic asset selection, rotated
  bounds, collision recovery, containment, mesh support, window openings and
  assemblies, saved geometry, lighting, and animation visibility.
- Animation: cyclic opacity, the five-visible-object limit, loop continuity,
  interpolation, encoded frame durations, and preview timing.
- Runtime and benchmark: count accounting, single-pass coordinates, request
  and seed identity, immutable plans, crash recovery, budget reservations,
  archival integrity, public field filtering, and review/export contracts.

New core cases live here; tests for the serverless implementation remain in
[`serverless/tests/`](../serverless/tests/). Blender-only tests live in
[`tests/blender/`](blender/) and run in Blender rather than with a fake
`bpy` module. Fixtures use analytic expectations or small controlled inputs.
Source-hash checks detect drift and complement the behavioral tests.

A passing suite establishes the tested contracts. It does not prove that every
possible generated room is plausible. A failing generation regression blocks
the next campaign; preserve its input and decide whether the completed cohort
is affected before changing the model or regenerating rooms.
