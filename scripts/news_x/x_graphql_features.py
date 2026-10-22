"""The captured X web GraphQL contract — query ids, features, field toggles.

Nothing here is guessed. Every value was recorded off a real Chromium session
opening https://x.com/lookonchain (`x_capture.py` -> `fixtures/usertweets_live.json`,
2026-10-22). X ships a new web build every few weeks and a stale id does not
error: it returns a media-only or empty timeline, which is why the ids live in
exactly one place and carry their capture date.

To refresh: `python3 x_discover.py` (drives the browser, rewrites the fixture).
Env overrides: X_QID_TWEETS, X_QID_USER, X_FEATURES_JSON, X_FIELD_TOGGLES_JSON.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CAPTURED_AT = "2026-10-22T19:45:53Z"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "usertweets_live.json"

#: Public web-client bearer (ships in x.com's bundle; not an account credential).
BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4F"
    "A33AGWWjCpTnA"
)
GRAPHQL = "https://x.com/i/api/graphql"
ACTIVATE = "https://api.twitter.com/1.1/guest/activate.json"

#: UserByScreenName — live id captured alongside the timeline request.
QID_USER = "AMIBMjtxEEATh4z8V9GtRg"
#: The profile timeline. X renamed the op: the web client now calls
#: UserOriginalsTimeline, not UserTweets — the old op is what returned media
#: only / `timeline_empty` for handles with replies, and it is gone from the
#: bundle, so its id can never be right again.
OPERATION = "UserOriginalsTimeline"
QID_TWEETS = "X9t-YTjvUpLaif19q3RrRw"

#: Variables the browser sent; count/cursor are set per request.
VARIABLES: dict[str, object] = {
    "userId": "",
    "count": 20,
    "includePromotedContent": True,
    "withQuickPromoteEligibilityTweetFields": True,
    "withVoice": True,
}

#: 40 feature flags — a missing key is a 400, an extra key is ignored.
FEATURES: dict[str, bool] = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "rweb_sports_post_context_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_nested_quote_preview_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

#: Sent as `fieldToggles`; absence is tolerated, presence avoids a second round trip.
FIELD_TOGGLES: dict[str, bool] = {"withPayments": False, "withArticlePlainText": False}


def _env_json(name: str) -> dict:
    """Env override for a JSON object; a broken value is ignored, never fatal."""
    try:
        value = json.loads(os.environ.get(name, "") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def contract() -> dict[str, object]:
    """The contract in force: env overrides, then a newer fixture, then constants."""
    fields: dict[str, object] = {
        "query_id": os.environ.get("X_QID_TWEETS") or QID_TWEETS,
        "user_query_id": os.environ.get("X_QID_USER") or QID_USER,
        "operation": OPERATION,
        "features": _env_json("X_FEATURES_JSON") or dict(FEATURES),
        "field_toggles": _env_json("X_FIELD_TOGGLES_JSON") or dict(FIELD_TOGGLES),
        "variables": dict(VARIABLES),
        "captured_at": CAPTURED_AT,
    }
    try:
        saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fields
    if saved.get("query_id") and not os.environ.get("X_QID_TWEETS"):
        fields["query_id"] = saved["query_id"]
    if saved.get("features") and not os.environ.get("X_FEATURES_JSON"):
        fields["features"] = saved["features"]
    if saved.get("field_toggles") and not os.environ.get("X_FIELD_TOGGLES_JSON"):
        fields["field_toggles"] = saved["field_toggles"]
    fields["captured_at"] = saved.get("captured_at") or CAPTURED_AT
    return fields
