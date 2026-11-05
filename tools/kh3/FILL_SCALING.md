# KH3 fill profiling

KH3 uses standard Archipelago restrictive fill, progression balancing and
accessibility validation. Its starting-inventory overflow pass budgets the
player's progression/useful items against eligible checks; it does not use
the former capacity-matching solver or post-balancing junk repair. The current
policy and protected item rules are in
[fill safeguards](../../worlds/kh3/docs/development.md).

## Harness scope

`benchmark_fill_scaling.py` loads real KH3 options and real or synthetic foreign
slots in a disposable worker process. It does not launch a game/server, produce
an output pak or replace an installed APWorld.

- `--mode hook` isolates KH3's pre-fill hook.
- `--mode full-fill` includes AP early fill, progression balancing, finalization
  and accessibility validation, but does not generate output files.
- `--foreign-worlds` cycles the selected real games with their default options.
  Omitting it uses synthetic foreign slots as a control.
- `--foreign-rule local --local-every N` adds the local-item stress constraint
  to every Nth foreign slot. This is a benchmark fixture, not a KH3 fill rule.
- `--core-root` selects the AP checkout; KH3 still loads from this repository.

Use a concrete player YAML. The harness rejects unsupported nonempty plando,
item links and starting-inventory transformations rather than silently ignoring
them. Roll weighted settings before using this harness. Full supported-option
coverage belongs in the ordinary generation/regression tests.

## Run

From the repository root:

```powershell
$playerYaml = 'C:\path\player.yaml'
python tools/kh3/benchmark_fill_scaling.py --yaml $playerYaml --players 13 --kh3-players 5 --foreign-worlds shorthike timespinner v6 ror2 hk subnautica sm64ex undertale --mode full-fill --timeout 60 --output build/kh3-fill-benchmark/smoke.json
python tools/kh3/benchmark_fill_scaling.py --yaml $playerYaml --players 1700 --kh3-players 5 --foreign-worlds shorthike timespinner v6 ror2 hk subnautica sm64ex undertale --foreign-rule local --local-every 10 --mode hook --timeout 60 --output build/kh3-fill-benchmark/restricted.json
python tools/kh3/benchmark_fill_scaling.py --yaml $playerYaml --players 20 --kh3-players 5 --foreign-rule local --mode hook --profile --timeout 60 --output build/kh3-fill-benchmark/profile.json
```

Choose a smaller room before increasing scale. `--players` accepts several
counts; `--kh3-all-junk` is an explicit overflow stress fixture. Check the
current `--help` before reusing an old command. The retired `--skip-early-items`
and `--mode repair-fixture` arguments are not supported.

## Interpret results

`--timeout` covers the whole worker process, including imports/setup. A timeout
is an intentional nonzero result with saved stack samples; it does not prove
KH3's hook caused the delay. Distinguish time spent before the hook, inside it,
and in stock AP fill/balancing.

JSON summaries and per-case logs are written beside `--output`. Record source
and YAML identity, Python/core versions, game counts, pool sizes, stage timings
and starting-grant totals. Profiling adds overhead; compare it with another
profiled run, not an unprofiled timing. A forced timeout may prevent a `.prof`
file from being written.

Use matched fixtures, seed/hash settings and non-overlapping workers for before/
after comparisons. Separate hook-only results from full-fill results and full
seed-generation/output timings. Audit accessibility, item classifications,
strict junk placement and starting grants as well as elapsed time. A successful
single fixture is not a proof for arbitrary locality, plando or mixed worlds.

Old matching-solver timings, repaired-placement experiments, release-package
hashes and completed run logs remain in Git history. They do not describe the
current implementation or its performance.
