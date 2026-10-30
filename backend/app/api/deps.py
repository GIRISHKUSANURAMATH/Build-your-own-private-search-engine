"""Shared objects for route handlers (created once at startup, stored on app.state)."""

from fastapi import Request

from app.config import Settings
from app.crawler.jobs import CrawlManager
from app.indexing.indexer import SearchIndex
from app.search.search_engine import SearchEngine
from app.search.suggest import Suggester
from app.services.history_service import HistoryService
from app.services.index_service import IndexService


def settings(request: Request) -> Settings:
    return request.app.state.settings


def index(request: Request) -> SearchIndex:
    return request.app.state.index


def engine(request: Request) -> SearchEngine:
    return request.app.state.engine


def suggester(request: Request) -> Suggester:
    return request.app.state.suggester


def index_service(request: Request) -> IndexService:
    return request.app.state.index_service


def history(request: Request) -> HistoryService:
    return request.app.state.history


def crawl_manager(request: Request) -> CrawlManager:
    return request.app.state.crawl_manager
