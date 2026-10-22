"""Transport chain for X timelines: walk readers until enough posts arrive.

Default order is the one that actually works from this host:
    graphql (guest token, id-complete) -> pry (ours) -> brightdata
syndication / nitter / jina stay available via --stages but are NOT default:
syndication 429s from this egress, every nitter mirror is dead, and
fxtwitter exposes the profile but no timeline.

The chain is additive, not strictly first-wins: GraphQL returns the whole
id-complete timeline; if it fails, `min_posts` keeps walking (Pry can return
only the pinned post, with no ids and no dates) until the handle has enough
material. Pry is still always tried first — it is cheaper and it is ours.

Politeness and circuit breaking live in http_get; readers live in readers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from http_get import Limiter, get  # noqa: F401  (Limiter re-exported for callers)
from model import Post
from readers import STAGES

DEFAULT_STAGES: tuple[str, ...] = ("graphql", "pry", "brightdata")
MIN_POSTS = int(os.environ.get("X_MIN_POSTS", "3"))


@dataclass
class Chain:
    """Result of walking the chain for one handle."""

    handle: str
    posts: list[Post] = field(default_factory=list)
    route: str = ""
    errors: list[str] = field(default_factory=list)


def _merge(into: list[Post], posts: list[Post]) -> None:
    """Add posts not already seen; a pinned post is rendered by both readers."""
    seen = {post.dedupe_key for post in into}
    for post in posts:
        if post.dedupe_key not in seen:
            into.append(post)
            seen.add(post.dedupe_key)


def fetch_handle(
    handle: str, limiter: Limiter, stages: tuple[str, ...] | None = None, min_posts: int = 0
) -> Chain:
    """Walk the stage chain, accumulating until `min_posts` or the chain ends."""
    proxy = os.environ.get("X_INGEST_PROXY") or None
    target = min_posts or MIN_POSTS
    chain = Chain(handle=handle)
    for stage in stages or DEFAULT_STAGES:
        reader = STAGES.get(stage)
        if reader is None:
            chain.errors.append(f"{stage}:unknown_stage")
            continue
        posts, err = reader(handle, limiter, proxy)
        if posts:
            _merge(chain.posts, posts)
            chain.route = f"{chain.route}+{posts[0].route or stage}".lstrip("+")
            if len(chain.posts) >= target:
                return chain
        else:
            chain.errors.append(f"{stage}:{err}")
    return chain
