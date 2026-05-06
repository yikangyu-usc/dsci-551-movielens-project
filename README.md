# DSCI 551 Project — MovieLens 32M Analytical API (DuckDB)

A FastAPI service backed by **DuckDB** running analytical queries over the
**MovieLens 32M** dataset (~32 million ratings). Built to demonstrate how
DuckDB's *columnar storage* and *vectorized execution* map onto the four
application operations required by the course project.

---

## 1. Quick Start

### 1.1 Install dependencies

```bash
pip install -r requirements.txt
```

### 1.2 Download the dataset

The raw CSVs are too large to commit. Download `ml-32m.zip` from
[grouplens.org/datasets/movielens](https://grouplens.org/datasets/movielens/)
and extract into:

```
data/ml-32m/
  ├── movies.csv
  ├── ratings.csv
  ├── tags.csv
  └── links.csv
```

### 1.3 Build the DuckDB database (one-time, ~1.4 GB)

```bash
python db.py
```

On first run this converts the CSVs to Parquet (columnar, typed), then
loads them into `movielens.duckdb`. Subsequent runs attach the existing
binary instantly.

### 1.4 Run the API

```bash
uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

- Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Demo dashboard UI:    [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)

### 1.5 Run the CLI demo

In a second terminal:

```bash
python demo.py
```

This exercises all four operations (O1–O4), prints timings + tables, and
fetches `EXPLAIN ANALYZE` plans so you can see DuckDB's physical
operators.

---

## 2. Application Operations

Four operation categories are implemented. Each one is paired with a
short mapping note explaining the DuckDB internal behavior it relies on.


| #   | Op                    | Endpoint                                                                                      | What it does                                                                       |
| --- | --------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| O1  | Group-by Aggregation  | `/o1/by-genre`, `/o1/by-year`, `/o1/by-genre-year`                                            | AVG(rating), COUNT(*) grouped by genre / year / both                               |
| O2  | Filter + Projection   | `/o2/movies`                                                                                  | Dynamic filters (genre, votes, rating range, year), returns only requested columns |
| O3  | Top-K Ranking         | `/o3/top-movies`, `/o3/top-genres`                                                            | Bayesian weighted ranking, popularity, or avg-rating                               |
| O4  | Time-Series Analytics | `/o4/decade-trends`, `/o4/activity-timeline`, `/o4/genre-over-time`, `/o4/yearly-genre-share` | Rating volume + avg by release decade, calendar year, or genre-over-time           |


### Utility endpoints

- `GET /` — dataset summary counts
- `GET /movies/search?q=...` — title search
- `GET /movies/{id}` — single movie + top tags
- `GET /ui` — browser dashboard
- `GET /explain?op=<name>&analyze=true|false` — returns DuckDB's physical
plan (and timings) for a preset query. Preset op names:
`o1_by_genre`, `o2_filter_movies`, `o3_top_movies_weighted`,
`o4_decade_trends`.

### Response shape

All O1–O4 endpoints (and `/movies/search`) return:

```json
{
  "operation": "O1 — Group-by Aggregation (by genre)",
  "data": {
    "metadata": { "duration_ms": 770.2, "row_count": 20 },
    "data": [ { "...": "..." } ]
  }
}
```

`metadata.duration_ms` is the per-call backend latency measured in the
DuckDB `execute()` call (so it excludes JSON serialization and
network). The UI and `demo.py` consume this field directly.

---

## 3. Mapping: Application Behavior ↔ DuckDB Internals

This is the core of the project. For each operation we state **what the
app does**, **what DuckDB does internally**, and **why that internal
behavior matters**.

### O1 — Group-by Aggregation by Genre

**Application behavior**

```sql
SELECT mg.genre, COUNT(*), AVG(r.rating)
FROM ratings r JOIN movie_genres mg ON r.movieId = mg.movieId
GROUP BY mg.genre;
```

**What DuckDB does internally**

- **Columnar storage.** `ratings` is stored column-by-column, so the
  scan reads only `rating` and `movieId` — not the 32M×N full rows.
- **Vectorized execution.** The query runs as a pipeline of operators
  (`TABLE_SCAN → HASH_JOIN → HASH_GROUP_BY → ORDER_BY`, verified via
  `EXPLAIN ANALYZE`), where each operator processes a fixed-size
  vector (DuckDB's `STANDARD_VECTOR_SIZE` = 2048 values) at a time,
  keeping data in CPU cache and amortizing per-tuple overhead.
- **Hash join, not zonemap-based join.** The `movieId` equality is
  resolved by `HASH_JOIN` (build side: `movie_genres`, probe side:
  `ratings`). Zonemap pruning helps for filter predicates on stored
  columns; it does not short-circuit joins. The join cost is O(build
  hash + probe scan) rather than a nested-loop lookup per row.

**Why it matters**

A row-based engine (MySQL InnoDB) would have to read every row's full
payload to extract one column; for a 32M-row aggregation that's
enormous wasted I/O. DuckDB's columnar + vectorized pipeline is the
reason O1 returns in sub-second time (measured: ~770 ms on cold
cache, faster once the OS page cache is warm) on a laptop. Note
that although the `ratings` table carries two ART secondary indexes
(`movieId`, `userId`), the planner does **not** use them for this
query — it picks `HASH_JOIN` because a full columnar scan + hash
join is cheaper than per-row index probes when most rows qualify.

---

### O2 — Filter + Projection (Top-rated movies with ≥ N votes)

**Application behavior** — dynamic WHERE / HAVING filters (genre, year
range, vote count, rating range) and the client can request a subset of
columns.

**What DuckDB does internally**

- **Projection pushdown**: only the columns the client asked for
  (`movieId`, `title`, `avg_rating`, …) plus the columns needed by
  `WHERE`/`GROUP BY` are materialized — other columns are never
  read from disk. Zonemap pruning would apply here if we filtered
  on a *stored* column (e.g. `rating`), but our year filter is a
  derived expression (`cast(regexp_extract(title, ...) AS INT)`),
  so row-group pruning does not apply and the full `title` column
  is scanned row-group by row-group.
- **Post-aggregation filter.** The plan shows
  `HASH_JOIN → HASH_GROUP_BY → FILTER → TOP_N`: HAVING is applied
  after the aggregate on the vectorized output, and `ORDER BY …
  LIMIT` is executed by DuckDB's `TOP_N` operator (a K-element
  heap), not a full sort.

**Why it matters**

Classic row-store databases must read full tuples even when you only
need two columns. Columnar storage + projection pushdown means O2's
cost is proportional to the columns you actually use, not the table
width. This is the key reason DuckDB is suitable for ad-hoc analytical
filters.

---

### O3 — Top-K Ranking (Bayesian Weighted Score)

**Application behavior** — compute a weighted score per movie
(`WR = (v/(v+m))·R + (m/(v+m))·C`) then return the top K. Used to
rank "all-time best" films without letting low-vote films dominate.

**What DuckDB does internally**

- **CTE materialization.** Because `movie_stats` is referenced twice
  (once in `scored`, once implicitly via the `g` cross-join), the
  plan contains a `CTE` node plus two `CTE_SCAN` readers — confirmed
  in the physical plan. (Single-use CTEs would be inlined by the
  optimizer instead.)
- **Top-K instead of full sort.** The plan contains a `TOP_N`
  operator: DuckDB maintains a K-element heap during the scan, so
  the qualifying rows are never fully sorted.
- **Parallel intra-query execution.** DuckDB parallelizes scans and
  hash group-bys across threads (defaults to physical core count);
  partial aggregates are merged before the final pipeline stages.

**Why it matters**

Top-K over 32M rows would require either a full external sort or a
pre-built covering index in a row store. DuckDB's vectorized Top-K
operator + parallel scan gives sub-second ranking *without relying
on any index* (the ART indexes on `ratings` exist but are unused
here), which is perfect for the exploratory/ad-hoc nature of an
analytical dashboard.

---

### O4 — Time-Series Analytics (Rating Trends by Decade / Year)

**Application behavior** — extract release year from the title, bucket
by decade, report rating volume + average over time. Also a calendar
variant (`to_timestamp(timestamp)`).

**What DuckDB does internally**

- **Scalar functions on columnar batches.** `regexp_extract`,
`cast(... AS INTEGER)`, and `extract(year FROM ...)` are applied
vectorized over entire column chunks, not row-by-row.
- **Hash-based grouping** on the derived decade column, same
`HASH_GROUP_BY` operator as O1.
- **Full scan beats the index.** For a query that visits most rows
  anyway, a full columnar scan is *faster* than an index traversal
  because it avoids random I/O. The DuckDB planner agrees — the
  `EXPLAIN` output for these O4 queries shows `TABLE_SCAN`, not an
  index-nested-loop plan, even though ART indexes on `ratings` are
  available.

**Why it matters**

Temporal "trend" queries are a classic analytical workload. Row-based
engines require either an index on a derived column (which is awkward)
or accept a full table scan with row-by-row function evaluation.
DuckDB makes full-scan aggregation the fast path, which is exactly
what time-series analytics needs.

---

## 4. DuckDB Internal Architecture (focus areas)

The project focuses on three internal focus areas:

1. **Storage — columnar row groups.** Each table is stored as a
   sequence of row groups; within a row group each column is stored
   contiguously as a typed, length-prefixed array (optionally encoded
   with RLE, dictionary, or bitpacking). The practical win is **not**
   that the file is smaller — our `movielens.duckdb` (1.3 GB) is
   actually larger than the raw CSVs (911 MB) because it stores
   per-column metadata (zonemap min/max per row group), a pre-exploded
   `movie_genres` lookup table, typed fixed-width values (e.g. every
   rating is 8 bytes as DOUBLE whereas "5.0" in CSV is 3 text bytes),
   plus two pre-existing ART secondary indexes on `ratings(movieId)`
   and `ratings(userId)`. The win is that **I/O at query time is
   proportional to the columns you read, not the table width**. O1's
   genre aggregation touches only `rating` and `movieId` — a small
   fraction of the 1.3 GB database. In a row store, the same query
   would have to pull full rows into memory just to project out those
   two columns.
2. **Execution — vectorized pipelines.** Instead of Volcano-style
   tuple-at-a-time iteration or whole-column materialization, DuckDB
   operators process fixed-size vectors (compile-time constant
   `STANDARD_VECTOR_SIZE = 2048` since v0.9; confirmed on v1.5.2 used
   here). This keeps hot data in L1/L2 cache and lets certain
   operators use SIMD.
3. **Indexing & pruning — zonemaps + optional ART.** DuckDB's
   primary pruning mechanism for analytical scans is the *zonemap*:
   every row group ships with min/max statistics that the scan uses
   to skip row groups whose range cannot satisfy a filter predicate
   on a stored column. DuckDB also supports ART (Adaptive Radix
   Tree) secondary indexes — our database happens to carry two on
   `ratings(movieId)` and `ratings(userId)` from an earlier build.
   **Notable demo point:** inspecting the `EXPLAIN` plans shows the
   planner chose `HASH_JOIN` over index nested-loop for every one
   of O1–O4, because for scans that touch most of the table the
   hash-join cost beats per-row index probes. This is precisely
   the OLAP design decision — zonemaps + hash joins, not random
   B-tree lookups.

---

## 5. Comparison with MySQL and MongoDB


| Dimension                 | **DuckDB (this project)**                        | **MySQL (InnoDB)**                        | **MongoDB**                                      |
| ------------------------- | ------------------------------------------------ | ----------------------------------------- | ------------------------------------------------ |
| Storage                   | Columnar row groups                              | Row-based clustered B-tree                | BSON documents in collections                    |
| Index structure           | Zonemaps (min/max) + optional ART                | Clustered B-tree + secondary B-trees      | B-tree, hash, wildcard, compound                 |
| Execution                 | Vectorized (batches of 2048)                     | Tuple-at-a-time iterator                  | Aggregation pipeline (tuple-at-a-time)           |
| Aggregation over 32M rows | Full columnar scan is *the fast path*            | Requires covering index or slow full scan | Aggregation pipeline must read full documents    |
| Projection cost           | Only requested columns are read                  | Full row is read even for 1 column        | Full document is read unless `$project` is early |
| Writes                    | Append-optimized, weaker point-update throughput | Strong OLTP point-update/commit perf      | Strong document-update perf                      |
| Target workload           | OLAP / embedded analytics                        | OLTP                                      | Flexible-schema app data                         |


**Takeaway:** the same O1 genre-aggregation query that runs in
sub-second time on DuckDB (without any index) would either (a)
require reading all 32M full rows in MySQL unless a precisely-chosen
covering index exists, or (b) require streaming every BSON document
through MongoDB's aggregation pipeline. DuckDB's columnar +
vectorized design *is* the optimization.

---

## 6. Demo Walkthrough (for live presentation)

Recommended 5–10 min script:

1. **Show the UI** — open `http://127.0.0.1:8000/ui`, run O1 by
   genre twice. First call is slower (cold cache, ~700–900 ms on
   32M rows); the second hits the OS page cache and is noticeably
   faster — a good moment to explain columnar scan vs. cache.
2. **Run `/explain?op=o1_by_genre`** — show the physical plan. The
   actual operators you'll see are `TABLE_SCAN`, `HASH_JOIN`,
   `HASH_GROUP_BY`, `PROJECTION`, `ORDER_BY`. This is the concrete
   evidence for the columnar + vectorized claim.
3. **Run `demo.py`** in a second terminal — walks through all four
  operations + EXPLAIN with mapping notes printed alongside.
4. **Wrap up with the comparison table** — why MySQL/MongoDB would
  not serve this workload as efficiently.

---

## 7. Files

- `api.py` — FastAPI app, O1–O4 endpoints, `/ui`, `/explain`
- `db.py` — DuckDB initialization (CSV → Parquet → DuckDB)
- `demo.py` — CLI demo exercising all four operations + EXPLAIN
- `requirements.txt` — Python dependencies
- `data/ml-32m/` — source CSVs (downloaded separately)
- `data/parquet/` — intermediate Parquet files (generated)
- `movielens.duckdb` — persistent DuckDB database (generated)

