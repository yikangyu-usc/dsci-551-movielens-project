import duckdb
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "movielens.duckdb")
DATA_DIR = os.path.join(BASE_DIR, "data", "ml-32m")
PARQUET_DIR = os.path.join(BASE_DIR, "data", "parquet") 

def get_connection(read_only=False):
    db_path = DB_PATH.replace("\\", "/")
    return duckdb.connect(db_path, read_only=read_only)

def init_db():
    print("\n" + "="*60)
    print("🚀 CineLens Database Initialization System")
    print("="*60)
    
    overall_start = time.perf_counter() # Start global timer

    if not os.path.exists(PARQUET_DIR):
        os.makedirs(PARQUET_DIR)

    # --- Check for existing optimized database ---
    if os.path.exists(DB_PATH):
        con = get_connection(read_only=True)
        try:
            # Verify data integrity
            count = con.execute("SELECT count(*) FROM ratings").fetchone()[0]
            con.close()
            if count > 0:
                print(f"✅ System already optimized. Loaded {count:,} ratings.")
                print(f"⏱️  Warm startup time: {time.perf_counter() - overall_start:.4f} seconds")
                print("="*60 + "\n")
                return
        except:
            con.close()

    # --- Execute Storage Reorganization Logic ---
    print("⚠️  Optimized data not found. Initializing Storage Reorganization...")
    print("📦 Converting Raw CSVs to Binary Columnar Parquet format...")
    
    con = get_connection(read_only=False)
    tables = ["movies", "ratings", "tags", "links"]
    
    for table in tables:
        step_start = time.perf_counter() # Start step timer
        csv_path = os.path.join(DATA_DIR, f"{table}.csv").replace("\\", "/")
        parquet_path = os.path.join(PARQUET_DIR, f"{table}.parquet").replace("\\", "/")
        
        # 1. Transform CSV to Parquet (Core Optimization Step)
        if not os.path.exists(parquet_path):
            print(f"  ➜ Processing {table:8} ...", end="", flush=True)
            con.execute(f"COPY (SELECT * FROM read_csv_auto('{csv_path}')) TO '{parquet_path}' (FORMAT PARQUET)")
            
        # 2. Register/Load Table into DuckDB
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM '{parquet_path}'")
        
        step_duration = time.perf_counter() - step_start
        print(f" Done! ({step_duration:.2f}s)")

    # 3. Create Auxiliary Lookup Tables
    print("🔍 Building high-performance Genre index...", end="")
    index_start = time.perf_counter()
    con.execute("""
        CREATE OR REPLACE TABLE movie_genres AS 
        SELECT movieId, unnest(string_split(genres, '|')) AS genre FROM movies
    """)
    print(f" Done! ({time.perf_counter() - index_start:.2f}s)")

    total_duration = time.perf_counter() - overall_start
    print("-" * 60)
    print(f"🎉 DATABASE INITIALIZATION COMPLETE!")
    print(f"⏱️  Total Processing Time: {total_duration:.2f} seconds")
    print(f"💾 Optimized DB File: {DB_PATH}")
    print("="*60 + "\n")
    con.close()

if __name__ == "__main__":
    init_db()