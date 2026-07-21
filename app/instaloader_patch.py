"""Temporary patch for instaloader's broken Post metadata fetching.

Around June 2026 Instagram deprecated the GraphQL doc_id ``8845758582119845``
(``xdt_shortcode_media``) that instaloader uses to fetch Post metadata, and started
requiring an ``X-CSRFToken`` header on the replacement endpoint. As of instaloader
4.15.2 (the latest release) every ``Post.from_shortcode()`` call fails with
``BadResponseException: Fetching Post metadata failed.``, preceded by a 403 from
https://www.instagram.com/graphql/query.

Upstream tracking:
  - Bug:  https://github.com/instaloader/instaloader/issues/2716
  - Fix:  https://github.com/instaloader/instaloader/pull/2706  (open, unmerged)

This module ports the parts of that PR the bot actually needs -- fetching metadata for
a single post/reel -- rather than vendoring the whole thing. Import it before using
instaloader; ``apply()`` is idempotent.

REMOVE THIS once instaloader ships a release containing the upstream fix: delete this
file and its import in bot.py, and bump instaloader in requirements.txt/pyproject.toml.
"""

import logging
from typing import Any, Dict

import instaloader
from instaloader.exceptions import BadResponseException
from instaloader.structures import Post

logger = logging.getLogger(__name__)

# doc_id of PolarisPostRootQuery, which replaced the deprecated xdt_shortcode_media query.
_POST_DOC_ID = "27128499623469141"

# Instagram's v1 media_type integers, mapped to the legacy GraphQL __typename values
# that the rest of instaloader still expects.
_MEDIA_TYPES = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}

_applied = False


def _patched_doc_id_graphql_query(self, doc_id, variables, referer=None):
    """``InstaloaderContext.doc_id_graphql_query`` with an X-CSRFToken header.

    The replacement endpoint rejects cookie-only authentication with a 403, so the
    csrftoken cookie has to be echoed back as a header. Anonymous sessions won't have
    one yet, so prime it with a request to the homepage first.
    """
    csrf = next((c.value for c in self._session.cookies
                 if c.name == "csrftoken" and c.value), None)
    if not csrf:
        self._session.get("https://www.instagram.com/", timeout=self.request_timeout)
        csrf = next((c.value for c in self._session.cookies
                     if c.name == "csrftoken" and c.value), "")

    ctx = instaloader.instaloadercontext
    with ctx.copy_session(self._session, self.request_timeout) as tmpsession:
        tmpsession.headers.update(self._default_http_header(empty_session_only=True))
        del tmpsession.headers["Connection"]
        del tmpsession.headers["Content-Length"]
        tmpsession.headers["authority"] = "www.instagram.com"
        tmpsession.headers["scheme"] = "https"
        tmpsession.headers["accept"] = "*/*"
        tmpsession.headers["x-csrftoken"] = csrf
        if referer is not None:
            tmpsession.headers["referer"] = ctx.urllib.parse.quote(referer)

        variables_json = ctx.json.dumps(variables, separators=(",", ":"))

        resp_json = self.get_json(
            "graphql/query",
            params={"variables": variables_json, "doc_id": doc_id, "server_timestamps": "true"},
            session=tmpsession,
            use_post=True,
        )
    if "status" not in resp_json:
        self.error('GraphQL response did not contain a "status" field.')
    return resp_json


def _patched_obtain_metadata(self):
    """``Post._obtain_metadata`` against the new endpoint.

    The replacement returns data in Instagram's v1/iPhone format, so translate it back
    into the legacy GraphQL field names the rest of instaloader reads.
    """
    if self._full_metadata_dict:
        return

    resp = self._context.doc_id_graphql_query(
        _POST_DOC_ID,
        {
            "shortcode": self.shortcode,
            "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
        },
    )

    web_info = (resp.get("data") or {}).get("xdt_api__v1__media__shortcode__web_info") or {}
    items = web_info.get("items")
    if not items:
        raise BadResponseException("Fetching Post metadata failed.")

    media = items[0]
    media_type = media.get("media_type")
    typename = _MEDIA_TYPES.get(media_type)
    if not typename:
        raise BadResponseException(f"Unknown media_type in metadata: {media_type}.")

    pic_json: Dict[str, Any] = {
        "shortcode": media["code"],
        "id": media["pk"],
        "__typename": typename,
        "is_video": media_type == 2,
        "taken_at_timestamp": media["taken_at"],
        "owner": {
            "id": media["user"]["pk"],
            "username": media["user"].get("username", ""),
            "full_name": media["user"].get("full_name", ""),
        },
    }

    candidates = (media.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        pic_json["display_url"] = candidates[0]["url"]
    video_versions = media.get("video_versions") or []
    if video_versions:
        pic_json["video_url"] = video_versions[0]["url"]
    if media.get("video_duration") is not None:
        pic_json["video_duration"] = media["video_duration"]
    if media.get("view_count") is not None:
        pic_json["video_view_count"] = media["view_count"]
    if media.get("play_count") is not None:
        pic_json["video_play_count"] = media["play_count"]

    caption = media.get("caption")
    caption_text = caption.get("text") if isinstance(caption, dict) else None
    pic_json["edge_media_to_caption"] = (
        {"edges": [{"node": {"text": caption_text}}]} if caption_text is not None
        else {"edges": []}
    )
    pic_json["edge_media_preview_like"] = {"count": media.get("like_count") or 0}
    pic_json["edge_media_to_parent_comment"] = {
        "count": media.get("comment_count") or 0,
        "edges": [],
    }
    if media.get("accessibility_caption") is not None:
        pic_json["accessibility_caption"] = media["accessibility_caption"]
    if media.get("location"):
        pic_json["location"] = media["location"]

    # Carousel posts ("sidecars") nest their children; the bot picks the first video out
    # of these, so they still need translating.
    carousel = media.get("carousel_media") or []
    if carousel:
        carousel_nodes = []
        for item in carousel:
            item_type = item.get("media_type", 1)
            node: Dict[str, Any] = {
                "shortcode": item.get("code", ""),
                "__typename": _MEDIA_TYPES.get(item_type, "GraphImage"),
                "is_video": item_type == 2,
            }
            item_candidates = (item.get("image_versions2") or {}).get("candidates") or []
            node["display_url"] = item_candidates[0]["url"] if item_candidates else ""
            item_videos = item.get("video_versions") or []
            node["video_url"] = item_videos[0]["url"] if item_videos else None
            carousel_nodes.append({"node": node})
        pic_json["edge_sidecar_to_children"] = {"edges": carousel_nodes}

    self._full_metadata_dict = pic_json
    if self.shortcode != self._full_metadata_dict["shortcode"]:
        self._node.update(self._full_metadata_dict)
        raise instaloader.exceptions.PostChangedException


def apply() -> None:
    """Monkeypatch instaloader in place. Safe to call more than once."""
    global _applied
    if _applied:
        return

    instaloader.instaloadercontext.InstaloaderContext.doc_id_graphql_query = (
        _patched_doc_id_graphql_query
    )
    Post._obtain_metadata = _patched_obtain_metadata
    _applied = True
    logger.info(
        "Applied instaloader post-metadata patch (upstream PR #2706); "
        "remove once instaloader releases the fix"
    )
