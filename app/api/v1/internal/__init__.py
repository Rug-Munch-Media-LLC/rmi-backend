"""Internal API routers — news feed, admin, pipeline endpoints."""

from app.mount import register_external_router

register_external_router("app.api.v1.internal.news_feed")
