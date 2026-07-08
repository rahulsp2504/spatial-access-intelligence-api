from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.init_db import init_db
from app.routers import coverage, facilities, isochrone


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap DB + PostGIS on startup."""
    await init_db()
    yield


app = FastAPI(
    title="Spatial Access Intelligence API",
    description="""
## Overview

A spatial analytics backend answering the question:
**Who can access what — and where are the gaps?**

Built with **PostGIS**, **osmnx**, and **FastAPI**. Demonstrates three
patterns that distinguish production geospatial engineers from CRUD developers:

| Pattern | Implementation |
|---|---|
| Network isochrones | osmnx ego-graph + Delaunay alpha-shape |
| GIST-indexed k-NN | ST_DWithin pre-filter → ST_Distance sort |
| Spatial set algebra | ST_Union + ST_Difference for gap analysis |

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/isochrone` | Network-based reachability polygon |
| `GET` | `/facilities/nearest` | K-nearest POIs via GIST index |
| `GET` | `/facilities/query-plan` | Live EXPLAIN ANALYZE output |
| `POST` | `/coverage/gap` | Uncovered-area polygon + coverage % |
| `GET` | `/health` | Liveness probe |

## Dataset

Seeded with OSM POIs for **Orange County, CA** (hospitals, grocery stores,
EV chargers, schools) via `osmnx.features_from_place()`.
Grocery layer extended with California food-desert data.

## Source

GitHub: [github.com/rahulsp2504/spatial-access-intelligence-api](https://github.com/rahulsp2504)
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    contact={
        "name": "Rahul Sharad Pandit",
        "url": "https://github.com/rahulsp2504",
        "email": "rspandit@uci.edu",
    },
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(isochrone.router)
app.include_router(facilities.router)
app.include_router(coverage.router)


@app.get("/health", tags=["Meta"], summary="Liveness probe")
async def health():
    return {"status": "ok", "service": "spatial-access-intelligence-api", "version": "1.0.0"}
