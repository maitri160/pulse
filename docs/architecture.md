# Pulse architecture

## System overview

Everything inside the box runs in Docker Compose. The API and the workers never call each
other directly: they share state through Postgres (source of truth) and Redis (task queue
and cache).

```mermaid
flowchart LR
    subgraph ext[External]
        RSS[(RSS feeds<br/>30-100)]
        LLM[LLM API<br/>free tier]
    end

    subgraph compose[Docker Compose]
        Beat[Celery Beat<br/>scheduler]
        Redis[(Redis<br/>task queue + cache)]
        Worker[Celery workers]
        PG[(Postgres 16<br/>+ pgvector)]
        API[FastAPI<br/>JSON API + HTMX UI]
    end

    User([Browser])

    Beat -- "every 5 min:<br/>dispatch_due_feeds" --> Redis
    Redis -- "tasks" --> Worker
    Worker -- "HTTP GET<br/>(ETag / Last-Modified)" --> RSS
    Worker -- "articles, events,<br/>embeddings" --> PG
    Worker -- "summarize event" --> LLM
    User -- "GET /feed" --> API
    API -- "cached ranked feed" --> Redis
    API -- "query events" --> PG
```

## Article pipeline

What happens to each article a worker fetches. Colours show which week builds each step.

```mermaid
flowchart TD
    A[Fetch feed<br/>feedparser, conditional GET] --> B[Extract full text<br/>trafilatura]
    B --> C[Normalize<br/>canonical URL + content hash]
    C --> D{URL already<br/>stored?}
    D -- yes --> SKIP[Skip]
    D -- no --> E{Same content<br/>hash?}
    E -- yes --> DUP[Save as duplicate<br/>duplicate_of_id = original]
    E -- no --> F{MinHash LSH<br/>similarity >= 0.85?}
    F -- yes --> DUP
    F -- no --> ORIG[Save as original]

    ORIG --> G[Embed text<br/>MiniLM, 384 dims]
    G --> H{Nearest event<br/>close enough?}
    H -- yes --> J[Attach to event]
    H -- no --> K[Create new event]
    DUP -. "counts as another source<br/>for the original's event" .-> J

    J --> L{Meaningfully<br/>new articles?}
    K --> L
    L -- yes --> M[Regenerate LLM summary]
    L -- no --> N[Keep existing summary]

    classDef w1 fill:#dbeafe,stroke:#3b82f6,color:#111
    classDef w2 fill:#dcfce7,stroke:#22c55e,color:#111
    classDef w3 fill:#fef3c7,stroke:#f59e0b,color:#111
    class A,B,C,D,E,F,DUP,ORIG,SKIP w1
    class G,H,J,K w2
    class L,M,N w3
```

Legend: blue = Week 1 (ingest + dedupe), green = Week 2 (embeddings + clustering),
yellow = Week 3 (LLM summaries). Week 3 also adds ranking and caching on the read side
(API → Redis → Postgres in the overview).
