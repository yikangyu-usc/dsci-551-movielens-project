import requests
import time
from tabulate import tabulate

# API Configuration
API_URL = "http://127.0.0.1:8000"


def run_demo_query(endpoint, title, mapping_note=None):
    """Call an operation endpoint, print timings + a table + the internals
    mapping note that explains why DuckDB is well-suited for this query."""
    print("\n" + "=" * 75)
    print(f"🎬 Task: {title}")
    print("=" * 75)

    url = f"{API_URL}{endpoint}"
    client_start = time.perf_counter()

    try:
        response = requests.get(url)
        response.raise_for_status()
        res_json = response.json()

        # Endpoint payload shape: {"operation": ..., "data": {"metadata": ..., "data": [...]}}
        payload = res_json.get("data", {})
        metadata = payload.get("metadata", {})
        data_list = payload.get("data", [])

        backend_duration = metadata.get("duration_ms", "N/A")
        row_count = metadata.get("row_count", 0)
        client_duration = (time.perf_counter() - client_start) * 1000

        print(f"🚀 Backend Latency (DB): {backend_duration} ms")
        print(f"📡 Client Round-trip:    {client_duration:.2f} ms")
        print(f"📊 Dataset Scale:        32,000,000+ rows")
        print(f"✅ Result Count:         {row_count} records")
        if mapping_note:
            print(f"🧠 Internals Mapping:    {mapping_note}")
        print("-" * 75)

        if data_list:
            display_data = data_list[:10]
            print(tabulate(display_data, headers="keys", tablefmt="pretty"))
            if len(data_list) > 10:
                print(f"\n💡 Showing top 10 of {len(data_list)} rows.")
        else:
            print("⚠️ Warning: No matching data found.")

    except Exception as e:
        print(f"❌ Execution Failed: Ensure the FastAPI backend (uvicorn) is running.")
        print(f"   Error Details: {e}")


def run_explain(op, title):
    """Fetch DuckDB's physical plan for a preset op — shows the columnar
    scan + vectorized operators that make the query fast."""
    print("\n" + "=" * 75)
    print(f"🔬 EXPLAIN ANALYZE — {title}")
    print("=" * 75)
    try:
        res = requests.get(f"{API_URL}/explain", params={"op": op, "analyze": "true"})
        res.raise_for_status()
        j = res.json()
        print(f"op: {j['op']}   backend: {j['duration_ms']} ms")
        print("-" * 75)
        print("SQL:")
        print(j["sql"])
        print("-" * 75)
        print("Physical plan (DuckDB):")
        print(j["plan"])
    except Exception as e:
        print(f"❌ EXPLAIN failed: {e}")


if __name__ == "__main__":
    script_start = time.perf_counter()

    # ---------------------------------------------------------------------
    # O1 — Group-by Aggregation
    # ---------------------------------------------------------------------
    run_demo_query(
        "/o1/by-genre",
        "O1: Aggregate Rating Stats by Genre",
        mapping_note=(
            "Columnar scan reads only `rating` and `movieId` columns; "
            "vectorized HASH_GROUP_BY processes 2048 values per batch."
        ),
    )

    # ---------------------------------------------------------------------
    # O2 — Filter + Projection
    # ---------------------------------------------------------------------
    run_demo_query(
        "/o2/movies?genre=Drama&min_ratings=500&min_avg_rating=4.0&limit=20",
        "O2: Filter + Projection (Drama, ≥500 votes, ≥4.0 avg)",
        mapping_note=(
            "Predicate pushdown + zonemap pruning skip row groups whose "
            "min/max can't match the filter; only projected columns are read."
        ),
    )

    # ---------------------------------------------------------------------
    # O3 — Top-K Ranking (weighted Bayesian)
    # ---------------------------------------------------------------------
    run_demo_query(
        "/o3/top-movies?k=20",
        "O3: All-Time Top Movies (Weighted Bayesian Ranking)",
        mapping_note=(
            "CTE materializes in-memory, then window row_number() + "
            "ORDER BY/LIMIT streams through without sorting full set."
        ),
    )

    # ---------------------------------------------------------------------
    # O4 — Time-Series Analytics
    # ---------------------------------------------------------------------
    run_demo_query(
        "/o4/decade-trends",
        "O4: Decade-based Rating Trends (all genres)",
        mapping_note=(
            "regex on compact string column + integer grouping — all done "
            "column-at-a-time, no row reconstruction needed."
        ),
    )
    run_demo_query(
        "/o4/genre-over-time?genre=Sci-Fi",
        "O4: Sci-Fi Rating Trends Across Decades",
        mapping_note=(
            "Genre filter joins the exploded movie_genres lookup table; "
            "DuckDB's hash join runs in vectorized batches."
        ),
    )

    # ---------------------------------------------------------------------
    # EXPLAIN ANALYZE — prove the mapping
    # ---------------------------------------------------------------------
    run_explain("o1_by_genre", "O1 aggregation plan (shows HASH_GROUP_BY)")
    run_explain("o3_top_movies_weighted", "O3 weighted ranking plan (shows window + sort)")

    total = time.perf_counter() - script_start
    print("\n" + "=" * 75)
    print(f"🏁 Demo Finished in {total:.2f} seconds")
    print("ℹ️  Note: first query after cold start is slower (disk read);")
    print("    subsequent queries benefit from the OS page cache.")
    print("    See individual backend-latency lines above for per-query timings.")
    print("=" * 75)
