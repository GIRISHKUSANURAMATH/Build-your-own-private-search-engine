"""
PySearch FastAPI application.

Run from the backend/ folder:
    uvicorn app.main:app --reload

We use an app *factory* (create_app) so tests can build an app with their own
Settings (e.g. a throwaway database) instead of the real one.

Startup (lifespan):
    1. migrate the database (init_db)
    2. load the inverted index from SQLite into memory
    3. index any documents that are new or changed since the last run
    4. create the search engine, suggester, services and crawl manager
"""

import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analyze, crawler, documents, health, index, search
from app.config import Settings, get_settings
from app.crawler.jobs import CrawlBusyError, CrawlManager
from app.crawler.security import BlockedURLError
from app.database.database import init_db
from app.database.repositories import DuplicateDocumentError, NotFoundError
from app.indexing.html_extractor import UnsupportedContentType
from app.indexing.indexer import SearchIndex
from app.search.search_engine import SearchEngine
from app.search.suggest import Suggester
from app.services.crawl_service import InvalidStatusTransition
from app.services.history_service import HistoryService
from app.services.index_service import IndexService
from app.utils.urls import InvalidURLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pysearch")


def create_app(settings: Settings | None = None, fetcher_factory: Callable | None = None,
               resolver: Callable | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Runs once at startup, before the first request.
        db = settings.DATABASE_PATH
        init_db(db)
        logger.info("Database ready at %s", db)

        search_index = SearchIndex()
        search_index.load(db)
        if search_index.needs_rebuild():
            logger.info("Analyzer settings changed since the index was built: rebuilding")
            search_index.rebuild(db)
        pending = search_index.index_pending(db)
        logger.info("Index loaded: %s documents (%s newly indexed)", search_index.doc_count, pending["indexed"])

        index_service = IndexService(db, search_index)
        app.state.index = search_index
        app.state.engine = SearchEngine(search_index, db)
        app.state.suggester = Suggester(search_index, db)
        app.state.index_service = index_service
        app.state.history = HistoryService(db)
        app.state.crawl_manager = CrawlManager(db, index_service, settings, fetcher_factory,
                                               **({"resolver": resolver} if resolver else {}))
        interrupted = app.state.crawl_manager.recover_interrupted()
        if interrupted:
            logger.info("Marked %s interrupted crawl job(s) as failed", interrupted)
        yield
        app.state.crawl_manager.wait(timeout=1)

    app = FastAPI(
        title=f"{settings.APP_NAME} API",
        version=settings.APP_VERSION,
        description="A private, local-first search engine built from scratch.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.started_at = time.monotonic()

    # CORS: only the frontend origins listed in .env may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    # Domain errors -> consistent JSON responses {"detail": "..."} with the right status code.
    def handler(status: int):
        async def _handle(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        return _handle

    app.add_exception_handler(NotFoundError, handler(404))
    app.add_exception_handler(DuplicateDocumentError, handler(409))
    app.add_exception_handler(InvalidStatusTransition, handler(409))
    app.add_exception_handler(CrawlBusyError, handler(409))
    app.add_exception_handler(InvalidURLError, handler(422))
    app.add_exception_handler(BlockedURLError, handler(422))
    app.add_exception_handler(UnsupportedContentType, handler(415))

    app.include_router(health.router)
    app.include_router(analyze.router)
    app.include_router(search.router)
    app.include_router(documents.router)
    app.include_router(index.router)
    app.include_router(crawler.router)
    return app


app = create_app()
