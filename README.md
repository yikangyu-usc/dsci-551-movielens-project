# DSCI 551 Project — MovieLens 32M Analytical API (DuckDB)

A FastAPI service backed by **DuckDB** running analytical queries over the
**MovieLens 32M** dataset (~32 million ratings). Built to demonstrate how
DuckDB's *columnar storage* and *vectorized execution* map onto the four
application operations required by the course project.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Setup & Installation](#2-setup--installation)
3. [Dataset Instructions](#3-dataset-instructions)
4. [Configuration](#4-configuration)
5. [Secret Keys / Credentials](#5-secret-keys--credentials)
6. [Running the Application](#6-running-the-application)
7. [Reproducing the Results](#7-reproducing-the-results)
8. [Troubleshooting](#8-troubleshooting)
9. [Application Operations (Reference)](#9-application-operations-reference)
10. [Mapping: Application Behavior ↔ DuckDB Internals](#10-mapping-application-behavior--duckdb-internals)
11. [DuckDB Internal Architecture (focus areas)](#11-duckdb-internal-architecture-focus-areas)
12. [Comparison with MySQL and MongoDB](#12-comparison-with-mysql-and-mongodb)
13. [Files](#13-files)

---

## 1. Prerequisites

| Requirement     | Minimum                                                                  |
| --------------- | ------------------------------------------------------------------------ |
| Operating sys.  | macOS, Linux, or Windows 10+ (tested on macOS 14 + Ubuntu 22.04)         |
| Python          | **3.9 or newer** (3.10 / 3.11 / 3.12 all work — `python3 --version`)     |
| pip             | 22+ (`pip --version`)                                                    |
| Free disk space | **~5 GB** (raw CSVs ~1.0 GB, Parquet ~0.5 GB, DuckDB binary ~1.4 GB)     |
| RAM             | 4 GB is enough; 8 GB recommended for smooth `EXPLAIN ANALYZE` runs       |
| Internet        | Required once, to download the MovieLens dataset (~265 MB zip)           |

No GPU, no Docker, no cloud account, and **no API keys** are required
(see [§5](#5-secret-keys--credentials)).

---

## 2. Setup & Installation

### 2.1 Clone the repository

```bash
git clone <this-repo-url>
cd Project
```

> The repository contains the source code (`api.py`, `db.py`, `demo.py`)
> and `requirements.txt`. The 1.4 GB DuckDB binary and the raw CSVs are
> **not** committed (see `.gitignore`); they are rebuilt locally by the
> commands below.

### 2.2 Create an isolated Python environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# or, on Windows:
# .venv\Scripts\activate
```

### 2.3 Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` pins the following libraries:

```
duckdb>=1.1.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
requests>=2.31.0
tabulate>=0.9.0
```

Verify the installation:

```bash
python -c "import duckdb, fastapi, uvicorn, requests, tabulate; print('OK')"
```

You should see `OK` printed.

---

## 3. Dataset Instructions

This project uses the **MovieLens 32M** dataset, which is ~265 MB
compressed (~1.0 GB extracted). It is **too large to commit to GitHub**,
so you must download it once from the official source.

### 3.1 Download

Download `ml-32m.zip` from the official GroupLens page:

- **Page**: [https://grouplens.org/datasets/movielens/](https://grouplens.org/datasets/movielens/)
- **Direct link**: [https://files.grouplens.org/datasets/movielens/ml-32m.zip](https://files.grouplens.org/datasets/movielens/ml-32m.zip)

> The dataset is freely redistributable for non-commercial / research use
> per the [MovieLens README license](https://files.grouplens.org/datasets/movielens/ml-32m-README.html).

### 3.2 Extract into the expected location

Unzip into the `data/` folder of this repository so the layout is exactly:

```
Project/
  data/
    ml-32m/
      ├── movies.csv          (~3   MB)
      ├── ratings.csv         (~870 MB, 32M rows)
      ├── tags.csv            (~38  MB)
      ├── links.csv           (~1.5 MB)
      └── README.txt          (dataset info)
```

One-liner (macOS / Linux, run from the project root):

```bash
mkdir -p data
curl -L -o /tmp/ml-32m.zip https://files.grouplens.org/datasets/movielens/ml-32m.zip
unzip /tmp/ml-32m.zip -d data/
```

Verify the four CSVs are present:

```bash
ls -lh data/ml-32m/*.csv
```

### 3.3 Build the DuckDB database (one-time, ~1–3 minutes)

```bash
python db.py
```

This script will:

1. Read each CSV with DuckDB's `read_csv_auto`.
2. Persist them as columnar **Parquet** files in `data/parquet/`.
3. Load the Parquet files into a single persistent **DuckDB** binary
   `movielens.duckdb` (~1.4 GB) at the project root.
4. Build a `movie_genres` lookup table by exploding the pipe-separated
   `genres` column.

Expected output ends with:

```
🎉 DATABASE INITIALIZATION COMPLETE!
⏱️  Total Processing Time: <NN.NN> seconds
💾 Optimized DB File: .../Project/movielens.duckdb
```

If the database file already exists, re-running `python db.py` will
detect it and exit immediately (warm start).

> **No external dataset upload is required from the grader** — the four
> CSV files in `data/ml-32m/` are the input, and `python db.py`
> deterministically produces the same DuckDB binary every time.

---

## 4. Configuration

There is **no configuration file to edit**. All paths are derived
relative to the project root by `db.py`:

| Path constant   | Default value                  | Purpose                                |
| --------------- | ------------------------------ | -------------------------------------- |
| `DB_PATH`       | `./movielens.duckdb`           | Persistent DuckDB binary               |
| `DATA_DIR`      | `./data/ml-32m/`               | Source CSVs (you populate in §3.2)     |
| `PARQUET_DIR`   | `./data/parquet/`              | Intermediate Parquet (auto-generated)  |

If you want to relocate the database (e.g. to an external SSD), edit the
constants at the top of [db.py](db.py) and the matching `DB_PATH` near
the top of [api.py](api.py). For graders, the defaults are correct and
no changes are needed.

The API server's host and port are passed on the `uvicorn` command line
(see §6). Default is `127.0.0.1:8000`.

---

## 5. Secret Keys / Credentials

**This project does NOT require any API keys, secret tokens,
credentials, or environment variables.**

- No third-party APIs are called at runtime.
- No `.env` file is read.
- No cloud services (AWS / GCP / Azure / OpenAI / Anthropic / etc.) are
  used.
- The MovieLens dataset is publicly downloadable without registration.
- DuckDB is an embedded, in-process database — there is no server, no
  user, no password.

If you find any reference to credentials in the code, it is a bug —
please report it. Nothing in this repository requires the grader to
contact us by email for keys.

---

## 6. Running the Application

After completing §2 and §3, the application runs in two parts.

### 6.1 Start the API server

From the project root:

```bash
uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

The first time it starts, DuckDB will memory-map the 1.4 GB binary
(takes a couple of seconds). When you see `Application startup complete.`
the service is ready.

Open these URLs in any browser:

- **Interactive Swagger / OpenAPI docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Demo dashboard UI**: [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)
- **Dataset summary (root)**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

You can also `curl` any endpoint, e.g.:

```bash
curl 'http://127.0.0.1:8000/o1/by-genre' | python -m json.tool | head -40
```

### 6.2 Run the CLI demo (in a second terminal)

With the API still running in the first terminal, open a second terminal
in the same project directory (re-activate the venv if you used one) and
run:

```bash
python demo.py
```

This walks through all four operations (O1–O4), prints timings and
result tables, and fetches `EXPLAIN ANALYZE` plans so you can see
DuckDB's physical operators (`TABLE_SCAN`, `HASH_JOIN`, `HASH_GROUP_BY`,
`TOP_N`, …).

---

## 7. Reproducing the Results

The full reproduction pipeline, from a fresh clone, is exactly:

```bash
# 1. Get the code
git clone <this-repo-url>
cd Project

# 2. Python env + dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Get the dataset (one-time, ~265 MB download)
mkdir -p data
curl -L -o /tmp/ml-32m.zip https://files.grouplens.org/datasets/movielens/ml-32m.zip
unzip /tmp/ml-32m.zip -d data/

# 4. Build the DuckDB binary (one-time, ~1–3 min)
python db.py

# 5. Launch the API server
uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Then, in a **separate terminal**, with `.venv` activated:

```bash
python demo.py
```

`demo.py` is the canonical reproduction script. A successful run will:

- Print a header for each of O1–O4.
- Show a tabulated result for each query.
- Report `duration_ms` for each backend call (typical numbers on a 2023
  laptop: O1 ≈ 700–900 ms cold / 200–400 ms warm, O2 ≈ 200–500 ms,
  O3 ≈ 800–1200 ms, O4 ≈ 600–900 ms — your numbers will differ but the
  *operator pipeline* should match).
- Print `EXPLAIN ANALYZE` plans showing the physical operators
  referenced in [§10](#10-mapping-application-behavior--duckdb-internals).

You can also reproduce visually by opening `http://127.0.0.1:8000/ui`
and clicking each of the O1 / O2 / O3 / O4 buttons.

---

## 8. Troubleshooting

| Symptom                                                            | Cause / Fix                                                                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: No module named 'duckdb'`                    | The venv is not activated, or `pip install -r requirements.txt` was skipped. Re-activate and reinstall.                |
| `IO Error: No files found that match the pattern ".../movies.csv"` | Step §3.2 was skipped. Make sure all four CSVs are inside `data/ml-32m/`.                                              |
| `db.py` looks stuck on `ratings`                                   | This is normal — `ratings.csv` is 32 M rows / ~870 MB and takes 30–90 s to parse on first run. Wait it out.            |
| Port 8000 already in use                                           | Another process owns it. Either stop that process, or run on a different port: `uvicorn api:app --port 8001`.          |
| `demo.py` says `Connection refused`                                | The API server (step §6.1) is not running, or is on a non-default port. Start it first, or edit `BASE_URL` in demo.py. |
| Database file missing after deleting `movielens.duckdb`            | Just re-run `python db.py` — it rebuilds from the Parquet files (or from the CSVs if Parquet is also missing).         |

---

## 9. Application Operations (Reference)

Four operation categories are implemented. Each one is paired with a
short mapping note explaining the DuckDB internal behavior it relies on
(see [§10](#10-mapping-application-behavior--duckdb-internals)).


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

## 10. Mapping: Application Behavior ↔ DuckDB Internals

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

## 11. DuckDB Internal Architecture (focus areas)

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

## 12. Comparison with MySQL and MongoDB


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

## 13. Files

- [api.py](api.py) — FastAPI app, O1–O4 endpoints, `/ui`, `/explain`
- [db.py](db.py) — DuckDB initialization (CSV → Parquet → DuckDB)
- [demo.py](demo.py) — CLI demo exercising all four operations + EXPLAIN
- [requirements.txt](requirements.txt) — Python dependencies
- `data/ml-32m/` — source CSVs (downloaded separately, see §3)
- `data/parquet/` — intermediate Parquet files (generated by `db.py`)
- `movielens.duckdb` — persistent DuckDB database (generated by `db.py`)
