# DSCI 551 Project — MovieLens API

FastAPI service backed by DuckDB over the MovieLens 32M dataset.

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Download the MovieLens 32M dataset**

   The raw CSVs are too large for this repo. Download `ml-32m.zip` from
   [grouplens.org/datasets/movielens](https://grouplens.org/datasets/movielens/)
   and extract so the layout is:
   ```
   data/ml-32m/
     ├── movies.csv
     ├── ratings.csv
     ├── tags.csv
     └── links.csv
   ```

3. **Build the DuckDB database** (one-time, ~1.4 GB output)
   ```bash
   python db.py
   ```

4. **Run the API**
   ```bash
   uvicorn api:app --reload
   ```

   Open http://127.0.0.1:8000/docs for the interactive API docs.

## Files

- `api.py` — FastAPI endpoints
- `db.py` — DuckDB loader
- `requirements.txt` — Python dependencies
