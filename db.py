import duckdb
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "movielens.duckdb")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ml-32m")


def get_connection(read_only=False):
    return duckdb.connect(DB_PATH, read_only=read_only)


def init_db():
    """Load CSV files into DuckDB tables (only runs if tables are empty)."""
    # Quick check with read-only connection first to avoid locking
    if os.path.exists(DB_PATH):
        try:
            ro = get_connection(read_only=True)
            tables = ro.sql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            if "ratings" in table_names:
                count = ro.sql("SELECT count(*) FROM ratings").fetchone()[0]
                if count > 0:
                    print(f"Database already loaded ({count:,} ratings). Skipping.")
                    ro.close()
                    return
            ro.close()
        except Exception:
            pass

    con = get_connection()

    print("Loading CSV files into DuckDB...")

    # Movies
    print("  Loading movies.csv...")
    con.sql(f"""
        CREATE OR REPLACE TABLE movies AS
        SELECT * FROM read_csv('{DATA_DIR}/movies.csv', auto_detect=true)
    """)

    # Ratings (largest table ~32M rows)
    print("  Loading ratings.csv (this may take a moment)...")
    con.sql(f"""
        CREATE OR REPLACE TABLE ratings AS
        SELECT * FROM read_csv('{DATA_DIR}/ratings.csv', auto_detect=true)
    """)

    # Tags
    print("  Loading tags.csv...")
    con.sql(f"""
        CREATE OR REPLACE TABLE tags AS
        SELECT * FROM read_csv('{DATA_DIR}/tags.csv', auto_detect=true)
    """)

    # Links
    print("  Loading links.csv...")
    con.sql(f"""
        CREATE OR REPLACE TABLE links AS
        SELECT * FROM read_csv('{DATA_DIR}/links.csv', auto_detect=true)
    """)

    # Pre-build movie_genres table for faster genre queries
    print("  Building movie_genres lookup table...")
    con.sql("""
        CREATE OR REPLACE TABLE movie_genres AS
        SELECT movieId, unnest(string_split(genres, '|')) AS genre
        FROM movies
    """)

    row_count = con.sql("SELECT count(*) FROM ratings").fetchone()[0]
    print(f"Done! Loaded {row_count:,} ratings into DuckDB.")
    con.close()


if __name__ == "__main__":
    init_db()
