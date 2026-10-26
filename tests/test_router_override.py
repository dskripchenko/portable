"""
The hand-written override, applied on top of the generated configuration.

The generated `caddy.json` is replaced on every change, so anything a person
needs — CORS above all — has to live somewhere this tool reads and never
writes. These tests are about that promise from both ends: what is written gets
into the document where it has to be, and what cannot be applied is refused by
name rather than ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from portable import paths
from portable.router import caddy

CORS = {
    "handle": [
        {
            "handler": "headers",
            "response": {"set": {"Access-Control-Allow-Origin": ["*"]}},
        }
    ]
}


def demo() -> caddy.Site:
    return caddy.Site(name="demo", root=Path("/srv/demo"), upstreams=["127.0.0.1:9001"])


def other() -> caddy.Site:
    return caddy.Site(name="other", root=Path("/srv/other"), upstreams=["127.0.0.1:9002"])


def subroutes(document: dict, server: str, name: str) -> list[dict]:
    routes = document["apps"]["http"]["servers"][server]["routes"]
    route = next(r for r in routes if r.get("@id", "").startswith(f"portable-site-{name}"))

    return route["handle"][0]["routes"]


def write(content: object) -> Path:
    target = paths.caddy_override()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")

    return target


class TestRoutes:
    def test_a_site_route_runs_before_files_and_php(self):
        # After the root is set — handlers need it — and before anything that
        # answers, or a `headers` handler would wrap nothing.
        override = caddy.Override(routes={"demo": [CORS]})
        inner = subroutes(caddy.config([demo()], override=override), caddy.SERVER, "demo")

        assert inner[0]["handle"][0]["handler"] == "vars"
        assert inner[1] == CORS

    def test_it_reaches_the_https_server_too(self):
        override = caddy.Override(routes={"demo": [CORS]})
        document = caddy.config([demo()], tls_listen=443, override=override)

        assert subroutes(document, caddy.TLS_SERVER, "demo")[1] == CORS

    def test_star_means_every_site_and_goes_first(self):
        mine = {"handle": [{"handler": "headers", "response": {"set": {"X-Mine": ["1"]}}}]}
        override = caddy.Override(routes={"*": [CORS], "demo": [mine]})
        document = caddy.config([demo(), other()], override=override)

        assert subroutes(document, caddy.SERVER, "demo")[1:3] == [CORS, mine]
        assert subroutes(document, caddy.SERVER, "other")[1] == CORS
        assert subroutes(document, caddy.SERVER, "other")[2] != mine

    def test_the_two_servers_do_not_share_objects(self):
        override = caddy.Override(routes={"demo": [CORS]})
        document = caddy.config([demo()], tls_listen=443, override=override)
        plain = subroutes(document, caddy.SERVER, "demo")[1]
        secure = subroutes(document, caddy.TLS_SERVER, "demo")[1]

        assert plain == secure
        assert plain is not secure
        assert plain is not CORS


class TestPatch:
    def test_objects_merge_and_the_rest_is_kept(self):
        patch = {"logging": {"logs": {"default": {"level": "DEBUG"}}}}
        document = caddy.config([demo()], override=caddy.Override(patch=patch))

        assert document["logging"]["logs"]["default"]["level"] == "DEBUG"
        assert document["admin"]["config"] == {"persist": False}

    def test_null_deletes(self):
        patch = {"logging": None}
        document = caddy.config([demo()], override=caddy.Override(patch=patch))

        assert "logging" not in document

    def test_the_arguments_are_left_alone(self):
        target = {"a": {"b": 1}}
        patch = {"a": {"c": [1]}}
        merged = caddy.merge_patch(target, patch)

        assert merged == {"a": {"b": 1, "c": [1]}}
        assert target == {"a": {"b": 1}}
        assert merged["a"]["c"] is not patch["a"]["c"]


class TestLoading:
    def test_no_file_is_no_override(self):
        assert caddy.load_override(paths.caddy_override()) is None

    def test_a_valid_file_is_read(self):
        loaded = caddy.load_override(write({"routes": {"demo": [CORS]}, "config": {"x": 1}}))

        assert loaded.routes_for("demo") == [CORS]
        assert loaded.patch == {"x": 1}

    def test_broken_json_names_the_place(self):
        with pytest.raises(caddy.InvalidOverride) as excinfo:
            caddy.load_override(write('{"routes": {'))

        assert "caddy.override.json" in str(excinfo.value)
        assert "line 1" in str(excinfo.value)

    def test_caddy_keys_at_the_top_level_are_pointed_under_config(self):
        # The likeliest mistake: a fragment of Caddy's own config pasted in.
        with pytest.raises(caddy.InvalidOverride) as excinfo:
            caddy.load_override(write({"apps": {}}))

        assert "'apps'" in str(excinfo.value)
        assert "`config`" in str(excinfo.value)

    def test_routes_must_be_lists_of_objects(self):
        with pytest.raises(caddy.InvalidOverride):
            caddy.load_override(write({"routes": {"demo": CORS}}))
