from fastapi import FastAPI, Query, HTTPException
from db import init_db, get_connection

app = FastAPI(title="MovieLens 32M Analysis API", version="1.0")


@app.on_event("startup")
def startup():
    init_db()
    app.state.db = get_connection(read_only=True)


@app.on_event("shutdown")
def shutdown():
    app.state.db.close()


def query(sql: str, params: list | None = None):
    """Execute a read query and return list of dicts."""
    result = app.state.db.execute(sql, params or []).fetchall()
    columns = [desc[0] for desc in app.state.db.description]
    return [dict(zip(columns, row)) for row in result]


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------

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
    rows = query(
        "SELECT movieId, title, genres FROM movies WHERE title ILIKE '%' || $1 || '%' LIMIT $2",
        [q, limit],
    )
    return rows


@app.get("/movies/{movie_id}")
def get_movie(movie_id: int):
    """Single movie detail with aggregate stats."""
    movie = query("SELECT movieId, title, genres FROM movies WHERE movieId = $1", [movie_id])
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    stats = query(
        """
        SELECT count(*) AS num_ratings, round(avg(rating), 2) AS avg_rating
        FROM ratings WHERE movieId = $1
        """,
        [movie_id],
    )
    top_tags = query(
        """
        SELECT tag, count(*) AS cnt
        FROM tags WHERE movieId = $1
        GROUP BY tag ORDER BY cnt DESC LIMIT 10
        """,
        [movie_id],
    )
    return {**movie[0], **stats[0], "top_tags": top_tags}


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
