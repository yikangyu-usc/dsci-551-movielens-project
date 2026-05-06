from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from db import init_db, get_connection
import time

app = FastAPI(title="MovieLens 32M Analysis API", version="1.0")


@app.on_event("startup")
def startup():

    init_db() 

    app.state.db = get_connection(read_only=True)
    
    app.state.db.execute("SELECT count(*) FROM ratings").fetchone()


@app.on_event("shutdown")
def shutdown():
    app.state.db.close()


def query(sql: str, params: list | None = None):
    """Execute a read query and return {'metadata': {...}, 'data': [...]}.

    The wrapper attaches per-call timing + row count so the UI / demo
    script can show backend latency without re-running the query.
    """
    start_time = time.perf_counter()
    result = app.state.db.execute(sql, params or []).fetchall()
    columns = [desc[0] for desc in app.state.db.description]
    duration_ms = (time.perf_counter() - start_time) * 1000
    return {
        "metadata": {
            "duration_ms": round(duration_ms, 2),
            "row_count": len(result),
        },
        "data": [dict(zip(columns, row)) for row in result],
    }


# SQL snippets reused by /explain so the plan reflects the same SQL the
# operation endpoints actually run.
EXPLAIN_QUERIES: dict[str, str] = {
    "o1_by_genre": """
        SELECT mg.genre, count(*) AS rating_count, avg(r.rating) AS avg_rating
        FROM ratings r
        JOIN movie_genres mg ON r.movieId = mg.movieId
        GROUP BY mg.genre
        ORDER BY rating_count DESC
    """,
    "o2_filter_movies": """
        SELECT m.movieId, m.title, avg(r.rating) AS avg_rating, count(*) AS rating_count
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
        GROUP BY m.movieId, m.title
        HAVING count(*) >= 50 AND avg(r.rating) >= 4.0
        ORDER BY avg(r.rating) DESC
        LIMIT 50
    """,
    "o3_top_movies_weighted": """
        WITH movie_stats AS (
            SELECT m.movieId, m.title, count(*) AS rating_count, avg(r.rating) AS avg_rating
            FROM ratings r JOIN movies m ON r.movieId = m.movieId
            GROUP BY m.movieId, m.title
            HAVING count(*) >= 50
        ),
        g AS (SELECT avg(avg_rating) AS global_avg FROM movie_stats)
        SELECT ms.*, (ms.rating_count*1.0/(ms.rating_count+50))*ms.avg_rating
             + (50*1.0/(ms.rating_count+50))*g.global_avg AS weighted_score
        FROM movie_stats ms, g
        ORDER BY weighted_score DESC
        LIMIT 20
    """,
    "o4_decade_trends": """
        SELECT
            (cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) / 10) * 10 AS decade,
            count(*) AS rating_count, avg(r.rating) AS avg_rating
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
        GROUP BY decade
        ORDER BY decade
    """,
}


@app.get("/explain")
def explain(
    op: str = Query(..., description=f"Preset op name. One of: {sorted(EXPLAIN_QUERIES)}"),
    analyze: bool = Query(True, description="Use EXPLAIN ANALYZE (runs the query)"),
):
    """Return DuckDB's physical plan (and optionally timings) for a preset
    query. Demonstrates columnar scan + vectorized execution operators.
    """
    if op not in EXPLAIN_QUERIES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown op '{op}'. Available: {sorted(EXPLAIN_QUERIES)}",
        )
    sql = EXPLAIN_QUERIES[op].strip()
    prefix = "EXPLAIN ANALYZE" if analyze else "EXPLAIN"
    start = time.perf_counter()
    rows = app.state.db.execute(f"{prefix} {sql}").fetchall()
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    # DuckDB returns rows like (explain_key, explain_value)
    plan_text = "\n\n".join(
        f"[{r[0]}]\n{r[1]}" if len(r) >= 2 else str(r[0]) for r in rows
    )
    return {
        "op": op,
        "analyze": analyze,
        "sql": sql,
        "duration_ms": duration_ms,
        "plan": plan_text,
    }


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------

@app.get("/ui", response_class=HTMLResponse)
def ui():
    """Minimal HTML dashboard for demoing the operations in demo.py."""
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>MovieLens 32M — Demo UI</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #222; }
  h1 { margin-bottom: 4px; }
  .sub { color: #666; margin-bottom: 20px; }
  .card { border: 1px solid #e3e3e3; border-radius: 8px; padding: 16px; margin: 14px 0; }
  .card h3 { margin: 0 0 8px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
  button { padding: 8px 14px; background: #1f6feb; color: #fff; border: 0; border-radius: 6px; cursor: pointer; }
  button:hover { background: #155ab6; }
  input, select { padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; }
  .meta { font-family: ui-monospace, Menlo, monospace; color: #333; background: #f5f7fa; padding: 8px; border-radius: 6px; margin-top: 8px; font-size: 13px; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; font-size: 14px; }
  th, td { border: 1px solid #e3e3e3; padding: 6px 10px; text-align: left; }
  th { background: #fafafa; }
  .err { color: #b00020; font-family: ui-monospace, Menlo, monospace; }
</style>
</head>
<body>
<h1>MovieLens 32M — Demo UI</h1>
<div class="sub">Sub-second queries over 32M ratings via DuckDB + FastAPI.</div>

<div class="card">
  <h3>O3 — Top Movies (weighted ranking)</h3>
  <div class="row">
    k: <input id="o3_k" type="number" value="20" min="1" max="200" style="width:70px">
    genre: <input id="o3_genre" placeholder="(optional, e.g. Sci-Fi)" style="width:220px">
    metric:
    <select id="o3_metric">
      <option value="weighted">weighted</option>
      <option value="popularity">popularity</option>
      <option value="avg_rating">avg_rating</option>
    </select>
    <button onclick="runO3()">Run</button>
  </div>
  <div id="o3_out"></div>
</div>

<div class="card">
  <h3>O4 — Genre Over Time</h3>
  <div class="row">
    genre: <input id="o4g_genre" value="Sci-Fi" style="width:220px">
    <button onclick="runO4Genre()">Run</button>
  </div>
  <div id="o4g_out"></div>
</div>

<div class="card">
  <h3>O4 — Decade Trends (all genres)</h3>
  <div class="row">
    <button onclick="runO4Decade()">Run</button>
  </div>
  <div id="o4d_out"></div>
</div>

<div class="card">
  <h3>O1 — Aggregate by Genre</h3>
  <div class="row">
    <button onclick="runO1()">Run</button>
  </div>
  <div id="o1_out"></div>
</div>

<div class="card">
  <h3>EXPLAIN ANALYZE — DuckDB Physical Plan</h3>
  <div class="sub">Shows columnar scan + vectorized operators for any preset op.</div>
  <div class="row">
    op:
    <select id="ex_op">
      <option value="o1_by_genre">o1_by_genre</option>
      <option value="o2_filter_movies">o2_filter_movies</option>
      <option value="o3_top_movies_weighted">o3_top_movies_weighted</option>
      <option value="o4_decade_trends">o4_decade_trends</option>
    </select>
    <label><input id="ex_analyze" type="checkbox" checked> run (EXPLAIN ANALYZE)</label>
    <button onclick="runExplain()">Explain</button>
  </div>
  <div id="ex_out"></div>
</div>

<script>
async function runQuery(url, outId) {
  const out = document.getElementById(outId);
  out.innerHTML = "Running…";
  const t0 = performance.now();
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const json = await res.json();
    const payload = json.data || {};
    const meta = payload.metadata || {};
    const rows = payload.data || [];
    const clientMs = (performance.now() - t0).toFixed(1);
    const metaHtml = `<div class="meta">`
      + `backend: ${meta.duration_ms ?? "?"} ms &nbsp;|&nbsp; `
      + `round-trip: ${clientMs} ms &nbsp;|&nbsp; `
      + `rows: ${meta.row_count ?? rows.length} &nbsp;|&nbsp; `
      + `url: ${url}`
      + `</div>`;
    out.innerHTML = metaHtml + renderTable(rows.slice(0, 25))
      + (rows.length > 25 ? `<div class="meta">Showing first 25 of ${rows.length} rows.</div>` : "");
  } catch (e) {
    out.innerHTML = `<div class="err">Error: ${e.message}</div>`;
  }
}
function renderTable(rows) {
  if (!rows.length) return "<div class='meta'>No rows.</div>";
  const cols = Object.keys(rows[0]);
  let h = "<table><thead><tr>" + cols.map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    h += "<tr>" + cols.map(c => `<td>${r[c] ?? ""}</td>`).join("") + "</tr>";
  }
  return h + "</tbody></table>";
}
function runO3() {
  const k = document.getElementById("o3_k").value || 20;
  const genre = document.getElementById("o3_genre").value.trim();
  const metric = document.getElementById("o3_metric").value;
  let url = `/o3/top-movies?k=${k}&metric=${encodeURIComponent(metric)}`;
  if (genre) url += `&genre=${encodeURIComponent(genre)}`;
  runQuery(url, "o3_out");
}
function runO4Genre() {
  const g = document.getElementById("o4g_genre").value.trim() || "Sci-Fi";
  runQuery(`/o4/genre-over-time?genre=${encodeURIComponent(g)}`, "o4g_out");
}
function runO4Decade() { runQuery("/o4/decade-trends", "o4d_out"); }
function runO1() { runQuery("/o1/by-genre", "o1_out"); }

async function runExplain() {
  const out = document.getElementById("ex_out");
  const op = document.getElementById("ex_op").value;
  const analyze = document.getElementById("ex_analyze").checked;
  out.innerHTML = "Running EXPLAIN…";
  try {
    const res = await fetch(`/explain?op=${op}&analyze=${analyze}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const j = await res.json();
    const meta = `<div class="meta">op: ${j.op} &nbsp;|&nbsp; analyze: ${j.analyze} &nbsp;|&nbsp; backend: ${j.duration_ms} ms</div>`;
    const sqlBox = `<div class="meta" style="white-space:pre-wrap">${escapeHtml(j.sql)}</div>`;
    const planBox = `<pre class="meta" style="white-space:pre-wrap; max-height:420px; overflow:auto">${escapeHtml(j.plan)}</pre>`;
    out.innerHTML = meta + "<b>SQL</b>" + sqlBox + "<b>Physical plan</b>" + planBox;
  } catch (e) {
    out.innerHTML = `<div class="err">Error: ${e.message}</div>`;
  }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
}
</script>
</body>
</html>"""


@app.get("/")
def root():
    """Dataset summary statistics."""
    stats = {}
    for table in ["movies", "ratings", "tags", "links"]:
        count = app.state.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        stats[table] = count
    return {"dataset": "MovieLens 32M", "table_counts": stats}


@app.get("/movies/search")
def search_movies(q: str = Query(..., description="Search keyword"), limit: int = 20):
    """Search movies by title."""
    return query(
        "SELECT movieId, title, genres FROM movies WHERE title ILIKE '%' || $1 || '%' LIMIT $2",
        [q, limit],
    )


@app.get("/movies/{movie_id}")
def get_movie(movie_id: int):
    """Single movie detail with aggregate stats."""
    movie_rows = query(
        "SELECT movieId, title, genres FROM movies WHERE movieId = $1",
        [movie_id],
    )["data"]
    if not movie_rows:
        raise HTTPException(status_code=404, detail="Movie not found")

    stats_rows = query(
        """
        SELECT count(*) AS num_ratings, round(avg(rating), 2) AS avg_rating
        FROM ratings WHERE movieId = $1
        """,
        [movie_id],
    )["data"]
    top_tags = query(
        """
        SELECT tag, count(*) AS cnt
        FROM tags WHERE movieId = $1
        GROUP BY tag ORDER BY cnt DESC LIMIT 10
        """,
        [movie_id],
    )["data"]
    return {**movie_rows[0], **stats_rows[0], "top_tags": top_tags}


# ===========================================================================
# O1: Group-by Aggregation
#     AVG(rating) and COUNT(*) across genres and release years
# ===========================================================================

@app.get("/o1/by-genre")
def o1_by_genre():
    """O1: Aggregate AVG(rating) and COUNT(*) grouped by genre."""
    rows = query("""
        SELECT
            mg.genre,
            count(*)                    AS rating_count,
            round(avg(r.rating), 2)     AS avg_rating,
            round(min(r.rating), 1)     AS min_rating,
            round(max(r.rating), 1)     AS max_rating,
            count(DISTINCT r.userId)    AS unique_users,
            count(DISTINCT r.movieId)   AS unique_movies
        FROM ratings r
        JOIN movie_genres mg ON r.movieId = mg.movieId
        GROUP BY mg.genre
        ORDER BY rating_count DESC
    """)
    return {"operation": "O1 — Group-by Aggregation (by genre)", "data": rows}


@app.get("/o1/by-year")
def o1_by_year():
    """O1: Aggregate AVG(rating) and COUNT(*) grouped by release year."""
    rows = query("""
        SELECT
            cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) AS release_year,
            count(*)                    AS rating_count,
            round(avg(r.rating), 2)     AS avg_rating,
            count(DISTINCT r.movieId)   AS unique_movies,
            count(DISTINCT r.userId)    AS unique_users
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
        GROUP BY release_year
        ORDER BY release_year
    """)
    return {"operation": "O1 — Group-by Aggregation (by release year)", "data": rows}


@app.get("/o1/by-genre-year")
def o1_by_genre_year(
    genre: str | None = Query(None, description="Filter to a specific genre"),
):
    """O1: Cross-tabulation — AVG(rating) and COUNT(*) by genre × release year."""
    if genre:
        rows = query("""
            SELECT
                mg.genre,
                cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) AS release_year,
                count(*)                AS rating_count,
                round(avg(r.rating), 2) AS avg_rating
            FROM ratings r
            JOIN movies m ON r.movieId = m.movieId
            JOIN movie_genres mg ON m.movieId = mg.movieId
            WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
              AND mg.genre = $1
            GROUP BY mg.genre, release_year
            ORDER BY release_year
        """, [genre])
    else:
        rows = query("""
            SELECT
                mg.genre,
                (cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) / 10) * 10 AS decade,
                count(*)                AS rating_count,
                round(avg(r.rating), 2) AS avg_rating
            FROM ratings r
            JOIN movies m ON r.movieId = m.movieId
            JOIN movie_genres mg ON m.movieId = mg.movieId
            WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
            GROUP BY mg.genre, decade
            ORDER BY mg.genre, decade
        """)
    return {"operation": "O1 — Group-by Aggregation (genre × year)", "data": rows}


# ===========================================================================
# O2: Filter + Projection
#     Dynamic filters (min votes, rating range, genre, year) with only
#     necessary columns returned
# ===========================================================================

@app.get("/o2/movies")
def o2_filter_movies(
    genre: str | None = Query(None, description="Filter by genre"),
    min_ratings: int = Query(50, description="Minimum number of ratings"),
    min_avg_rating: float = Query(0.0, description="Minimum average rating"),
    max_avg_rating: float = Query(5.0, description="Maximum average rating"),
    year_from: int | None = Query(None, description="Release year start"),
    year_to: int | None = Query(None, description="Release year end"),
    columns: str = Query(
        "movieId,title,avg_rating,rating_count",
        description="Comma-separated columns to return: movieId,title,genres,avg_rating,rating_count,release_year",
    ),
    limit: int = 50,
):
    """O2: Apply dynamic filters and return only projected columns."""
    # Build WHERE clauses dynamically
    conditions = [
        "regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''"
    ]
    params = []
    param_idx = 1

    if genre:
        conditions.append(f"mg.genre = ${param_idx}")
        params.append(genre)
        param_idx += 1
    if year_from:
        conditions.append(
            f"cast(regexp_extract(m.title, '\\((\\d{{4}})\\)', 1) AS INTEGER) >= ${param_idx}"
        )
        params.append(year_from)
        param_idx += 1
    if year_to:
        conditions.append(
            f"cast(regexp_extract(m.title, '\\((\\d{{4}})\\)', 1) AS INTEGER) <= ${param_idx}"
        )
        params.append(year_to)
        param_idx += 1

    having = [f"count(*) >= ${param_idx}"]
    params.append(min_ratings)
    param_idx += 1

    having.append(f"round(avg(r.rating), 2) >= ${param_idx}")
    params.append(min_avg_rating)
    param_idx += 1

    having.append(f"round(avg(r.rating), 2) <= ${param_idx}")
    params.append(max_avg_rating)
    param_idx += 1

    params.append(limit)
    limit_param = f"${param_idx}"

    join_genre = "JOIN movie_genres mg ON m.movieId = mg.movieId" if genre else ""

    where_clause = " AND ".join(conditions) if conditions else "TRUE"
    having_clause = " AND ".join(having)

    # All possible columns
    col_map = {
        "movieId": "m.movieId",
        "title": "m.title",
        "genres": "m.genres",
        "avg_rating": "round(avg(r.rating), 2) AS avg_rating",
        "rating_count": "count(*) AS rating_count",
        "release_year": "cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) AS release_year",
    }

    requested = [c.strip() for c in columns.split(",")]
    select_cols = []
    for c in requested:
        if c in col_map:
            select_cols.append(col_map[c])
    if not select_cols:
        select_cols = [col_map["movieId"], col_map["title"], col_map["avg_rating"], col_map["rating_count"]]

    sql = f"""
        SELECT {', '.join(select_cols)}
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        {join_genre}
        WHERE {where_clause}
        GROUP BY m.movieId, m.title, m.genres
        HAVING {having_clause}
        ORDER BY avg(r.rating) DESC
        LIMIT {limit_param}
    """

    rows = query(sql, params)
    return {
        "operation": "O2 — Filter + Projection",
        "filters_applied": {
            "genre": genre,
            "min_ratings": min_ratings,
            "min_avg_rating": min_avg_rating,
            "max_avg_rating": max_avg_rating,
            "year_from": year_from,
            "year_to": year_to,
        },
        "columns_returned": requested,
        "data": rows,
    }


# ===========================================================================
# O3: Top-K Ranking
#     Ranked movie lists by popularity, avg rating, or weighted score
# ===========================================================================

@app.get("/o3/top-movies")
def o3_top_movies(
    metric: str = Query(
        "weighted",
        description="Ranking metric: popularity, avg_rating, or weighted (Bayesian)",
    ),
    genre: str | None = Query(None, description="Filter by genre"),
    min_ratings: int = Query(50, description="Minimum ratings to qualify"),
    k: int = Query(20, description="Number of top results"),
):
    """O3: Top-K movies ranked by popularity, average rating, or weighted score.

    The 'weighted' metric uses a Bayesian average (IMDB formula):
        WR = (v / (v + m)) * R + (m / (v + m)) * C
    where v = vote count, m = minimum votes, R = movie avg, C = global avg.
    This prevents low-vote movies from dominating the ranking.
    """
    join_genre = "JOIN movie_genres mg ON m.movieId = mg.movieId" if genre else ""
    genre_filter = "AND mg.genre = $1" if genre else ""

    params = []
    param_idx = 1
    if genre:
        params.append(genre)
        param_idx += 1
    params.append(min_ratings)
    min_param = f"${param_idx}"
    param_idx += 1
    params.append(k)
    k_param = f"${param_idx}"

    if metric == "popularity":
        order_expr = "rating_count DESC"
    elif metric == "avg_rating":
        order_expr = "avg_rating DESC"
    else:
        order_expr = "weighted_score DESC"

    weighted_col = f""",
            round(
                (ms.rating_count * 1.0 / (ms.rating_count + {min_ratings})) * ms.avg_rating
              + ({min_ratings} * 1.0 / (ms.rating_count + {min_ratings})) * g.global_avg
            , 2) AS weighted_score""" if metric == "weighted" else ""

    sql = f"""
        WITH movie_stats AS (
            SELECT
                m.movieId,
                m.title,
                m.genres,
                count(*)                AS rating_count,
                round(avg(r.rating), 2) AS avg_rating
            FROM ratings r
            JOIN movies m ON r.movieId = m.movieId
            {join_genre}
            WHERE TRUE {genre_filter}
            GROUP BY m.movieId, m.title, m.genres
            HAVING count(*) >= {min_param}
        ),
        global AS (
            SELECT round(avg(avg_rating), 2) AS global_avg FROM movie_stats
        ),
        scored AS (
            SELECT
                ms.movieId,
                ms.title,
                ms.genres,
                ms.rating_count,
                ms.avg_rating
                {weighted_col}
            FROM movie_stats ms, global g
        )
        SELECT
            row_number() OVER (ORDER BY {order_expr}) AS rank,
            *
        FROM scored
        ORDER BY {order_expr}
        LIMIT {k_param}
    """

    rows = query(sql, params)
    return {
        "operation": f"O3 — Top-K Ranking (metric={metric})",
        "params": {"metric": metric, "genre": genre, "min_ratings": min_ratings, "k": k},
        "data": rows,
    }


@app.get("/o3/top-genres")
def o3_top_genres(
    metric: str = Query("popularity", description="popularity, avg_rating, or weighted"),
    k: int = Query(10, description="Number of top genres"),
):
    """O3: Top-K genres ranked by total ratings, avg rating, or weighted score."""
    if metric == "avg_rating":
        order = "avg_rating DESC"
    elif metric == "weighted":
        order = "weighted_score DESC"
    else:
        order = "rating_count DESC"

    rows = query(f"""
        WITH genre_stats AS (
            SELECT
                mg.genre,
                count(*)                  AS rating_count,
                round(avg(r.rating), 2)   AS avg_rating,
                count(DISTINCT r.movieId) AS unique_movies,
                round(avg(r.rating) * ln(count(*) + 1), 2) AS weighted_score
            FROM ratings r
            JOIN movie_genres mg ON r.movieId = mg.movieId
            GROUP BY mg.genre
        )
        SELECT
            row_number() OVER (ORDER BY {order}) AS rank,
            *
        FROM genre_stats
        ORDER BY {order}
        LIMIT $1
    """, [k])
    return {
        "operation": f"O3 — Top-K Genre Ranking (metric={metric})",
        "data": rows,
    }


# ===========================================================================
# O4: Time-Series Analytics
#     Rating trends over decades / years for historical consumption patterns
# ===========================================================================

@app.get("/o4/decade-trends")
def o4_decade_trends():
    """O4: Rating volume and average by release decade — how films from
    different eras are perceived."""
    rows = query("""
        SELECT
            (cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) / 10) * 10 AS decade,
            count(*)                  AS rating_count,
            round(avg(r.rating), 2)   AS avg_rating,
            count(DISTINCT r.movieId) AS movies_rated,
            count(DISTINCT r.userId)  AS active_users
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
        GROUP BY decade
        ORDER BY decade
    """)
    return {"operation": "O4 — Decade Trends (by release decade)", "data": rows}


@app.get("/o4/activity-timeline")
def o4_activity_timeline():
    """O4: When ratings were submitted — platform activity over calendar years."""
    rows = query("""
        SELECT
            extract(year FROM to_timestamp(timestamp))::INT AS activity_year,
            count(*)                  AS rating_count,
            round(avg(rating), 2)     AS avg_rating,
            count(DISTINCT userId)    AS active_users,
            count(DISTINCT movieId)   AS movies_rated
        FROM ratings
        GROUP BY activity_year
        ORDER BY activity_year
    """)
    return {"operation": "O4 — Activity Timeline (by rating year)", "data": rows}


@app.get("/o4/genre-over-time")
def o4_genre_over_time(
    genre: str = Query("Drama", description="Genre to track"),
):
    """O4: A single genre's rating volume and avg score across release decades."""
    rows = query("""
        SELECT
            (cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) / 10) * 10 AS decade,
            count(*)                AS rating_count,
            round(avg(r.rating), 2) AS avg_rating,
            count(DISTINCT r.movieId) AS movies_rated
        FROM ratings r
        JOIN movies m ON r.movieId = m.movieId
        JOIN movie_genres mg ON m.movieId = mg.movieId
        WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
          AND mg.genre = $1
        GROUP BY decade
        ORDER BY decade
    """, [genre])
    return {
        "operation": f"O4 — Genre Over Time ({genre})",
        "data": rows,
    }


@app.get("/o4/yearly-genre-share")
def o4_yearly_genre_share(
    start_year: int = Query(1990, description="Start release year"),
    end_year: int = Query(2023, description="End release year"),
):
    """O4: Genre market share (% of ratings) by release year — shows how
    audience consumption patterns shift over time."""
    rows = query("""
        WITH yearly AS (
            SELECT
                cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) AS release_year,
                mg.genre,
                count(*) AS cnt
            FROM ratings r
            JOIN movies m ON r.movieId = m.movieId
            JOIN movie_genres mg ON m.movieId = mg.movieId
            WHERE regexp_extract(m.title, '\\((\\d{4})\\)', 1) != ''
              AND cast(regexp_extract(m.title, '\\((\\d{4})\\)', 1) AS INTEGER) BETWEEN $1 AND $2
            GROUP BY release_year, mg.genre
        )
        SELECT
            release_year,
            genre,
            cnt AS rating_count,
            round(cnt * 100.0 / sum(cnt) OVER (PARTITION BY release_year), 1) AS pct_share
        FROM yearly
        ORDER BY release_year, pct_share DESC
    """, [start_year, end_year])
    return {
        "operation": "O4 — Yearly Genre Share",
        "data": rows,
    }

