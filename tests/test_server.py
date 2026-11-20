import io
import json
import http.cookies
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_server import APIError, ComChat, create_wsgi_application


class ComChatAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ComChat(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, data=None, token=None, query=None):
        return self.app.dispatch(method, path, query or {}, data or {}, token)

    def register(self, username, display_name):
        status, body, headers = self.request("POST", "/api/register", {
            "username": username,
            "display_name": display_name,
            "password": "a-unique-test-password",
        })
        self.assertEqual(status, 201, body)
        cookie = http.cookies.SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        return body["user"], cookie["comchat_session"].value

    def login_token(self, username, password):
        status, _, headers = self.request("POST", "/api/login", {"username": username, "password": password})
        self.assertEqual(status, 200)
        cookie = http.cookies.SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        return cookie["comchat_session"].value

    def wsgi_request(self, wsgi, method, path, data=None, cookie=None, origin=None):
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "HTTP_HOST": "comchat.example",
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.url_scheme": "https",
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        if origin:
            environ["HTTP_ORIGIN"] = origin
        response = {}

        def start_response(status, headers):
            response["status"] = status
            response["headers"] = dict(headers)

        payload = b"".join(wsgi(environ, start_response))
        return response["status"], response["headers"], payload

    def test_account_creation_password_login_and_profile(self):
        user, token = self.register("morgan_1", "Morgan")
        self.assertEqual(user["display_name"], "Morgan")
        self.assertTrue(user["is_admin"])
        self.assertEqual(self.request("GET", "/api/me", token=token)[1]["user"]["username"], "morgan_1")
        status, _, headers = self.request("POST", "/api/login", {
            "username": "MORGAN_1",
            "password": "a-unique-test-password",
        })
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        second_token = self.login_token("morgan_1", "a-unique-test-password")
        self.assertEqual(self.request("POST", "/api/login", {
            "username": "morgan_1",
            "password": "wrong-password",
        })[0], 401)
        status, body, _ = self.request("PATCH", "/api/profile", {"display_name": "Morgan Lee"}, token)
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["display_name"], "Morgan Lee")
        self.assertEqual(self.request("POST", "/api/logout", token=token)[0], 200)
        self.assertEqual(self.request("GET", "/api/me", token=token)[0], 401)
        self.assertEqual(self.request("GET", "/api/me", token=second_token)[0], 200)

    def test_registration_validates_username_and_password(self):
        for username, password in [("ab", "a-unique-test-password"), ("invalid name", "a-unique-test-password"), ("goodname", "short")]:
            status, _, _ = self.request("POST", "/api/register", {
                "username": username,
                "display_name": "Name",
                "password": password,
            })
            self.assertEqual(status, 400)

    def test_wsgi_entry_serves_static_page_and_health_check(self):
        wsgi = create_wsgi_application(self.app)
        status, headers, body = self.wsgi_request(wsgi, "GET", "/")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"ComChat", body)
        self.assertIn("Content-Security-Policy", headers)
        status, _, body = self.wsgi_request(wsgi, "GET", "/healthz")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, b"ok")

    def test_wsgi_entry_supports_auth_cookie_and_origin_protection(self):
        wsgi = create_wsgi_application(self.app)
        with patch.dict(os.environ, {"COMCHAT_SECURE_COOKIES": "1"}):
            status, headers, body = self.wsgi_request(wsgi, "POST", "/api/register", {
                "username": "wsgi_member",
                "display_name": "WSGI Member",
                "password": "a-unique-test-password",
            })
        self.assertEqual(status, "201 Created")
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertEqual(json.loads(body)["user"]["username"], "wsgi_member")
        cookie = http.cookies.SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        token = cookie["comchat_session"].value

        status, _, body = self.wsgi_request(
            wsgi,
            "GET",
            "/api/me",
            cookie=f"comchat_session={token}",
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(json.loads(body)["user"]["is_admin"])

        status, _, body = self.wsgi_request(
            wsgi,
            "POST",
            "/api/profile",
            {"display_name": "Bad origin"},
            cookie=f"comchat_session={token}",
            origin="https://attacker.example",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("origin", json.loads(body)["error"])

    def test_google_sign_in_creates_and_reuses_the_same_account(self):
        claims = {
            "sub": "google-user-123",
            "name": "Google Friend",
            "aud": "configured-client-id",
            "iss": "https://accounts.google.com",
            "exp": "9999999999",
            "email_verified": "true",
        }
        with patch.object(self.app, "_verify_google_credential", return_value=claims):
            status, first, headers = self.request("POST", "/api/google", {"credential": "verified-token"})
            self.assertEqual(status, 200)
            self.assertEqual(first["user"]["display_name"], "Google Friend")
            self.assertTrue(first["user"]["is_admin"])
            self.assertIn("HttpOnly", headers["Set-Cookie"])

            status, second, _ = self.request("POST", "/api/google", {"credential": "verified-token"})
            self.assertEqual(status, 200)
            self.assertEqual(first["user"]["id"], second["user"]["id"])

    def test_google_sign_in_requires_credential_and_server_configuration(self):
        self.assertEqual(self.request("POST", "/api/google", {})[0], 400)
        with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": ""}):
            self.assertEqual(
                self.request("POST", "/api/google", {"credential": "token"})[0],
                503,
            )

    def test_google_credential_checks_audience_issuer_expiry_and_verified_email(self):
        claims = {
            "sub": "google-user-456",
            "aud": "configured-client-id",
            "iss": "accounts.google.com",
            "exp": "9999999999",
            "email_verified": "true",
        }
        with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "configured-client-id"}):
            with patch("app_server.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(claims).encode())) as fetch:
                self.assertEqual(self.app._verify_google_credential("credential")["sub"], claims["sub"])
                request = fetch.call_args.args[0]
                self.assertEqual(request.get_method(), "POST")
                self.assertNotIn("credential", request.full_url)

            for field, invalid_value in (
                ("aud", "another-client"),
                ("iss", "untrusted.example"),
                ("exp", "1"),
                ("email_verified", "false"),
            ):
                with self.subTest(field=field):
                    invalid_claims = {**claims, field: invalid_value}
                    with patch(
                        "app_server.urllib.request.urlopen",
                        return_value=io.BytesIO(json.dumps(invalid_claims).encode()),
                    ):
                        with self.assertRaises(APIError):
                            self.app._verify_google_credential("credential")

    def test_invite_joins_community_and_messages_sync_between_members(self):
        first, first_token = self.register("first_user", "First")
        second, second_token = self.register("second_user", "Second")
        self.assertTrue(first["is_admin"])
        self.assertFalse(second["is_admin"])
        status, created, _ = self.request("POST", "/api/communities", {"name": "Weekend crew"}, first_token)
        self.assertEqual(status, 201)
        community = created["community"]
        self.assertEqual([space["kind"] for space in community["spaces"]], ["text"])
        status, invite, _ = self.request("GET", f"/api/communities/{community['id']}/invite", token=first_token)
        self.assertEqual(status, 200)
        self.assertEqual(invite["invite_code"], community["invite_code"])
        self.assertEqual(self.request("POST", "/api/communities/join", {"invite_code": invite["invite_code"]}, second_token)[0], 200)

        space_id = community["spaces"][0]["id"]
        status, sent, _ = self.request("POST", f"/api/conversations/{space_id}/messages", {"text": "Hi everyone"}, first_token)
        self.assertEqual(status, 201)
        message_id = sent["message"]["id"]
        self.assertEqual(sent["message"]["author"], "First")
        synced = self.request("GET", f"/api/conversations/{space_id}/messages", token=second_token)[1]["messages"]
        self.assertEqual([message["text"] for message in synced], ["Hi everyone"])
        self.assertEqual(
            self.request("POST", f"/api/conversations/{space_id}/messages", {"text": "A reply from another member"}, second_token)[0],
            201,
        )
        new_messages = self.request(
            "GET",
            f"/api/conversations/{space_id}/messages",
            token=first_token,
            query={"after": [str(message_id)]},
        )[1]["messages"]
        self.assertEqual([message["text"] for message in new_messages], ["A reply from another member"])
        self.assertEqual(self.request("POST", f"/api/conversations/{space_id}/messages", {"text": "No access"}, token=None)[0], 401)
        self.assertEqual(self.request("POST", f"/api/conversations/{space_id}/messages", {"text": "No access"}, second_token)[0], 201)
        self.assertEqual(self.request("GET", f"/api/conversations/{space_id}/messages", token=second_token, query={"after": ["invalid"]})[0], 400)
        self.assertEqual(self.request("POST", f"/api/messages/{message_id}/reactions", {"emoji": "💜"}, second_token)[0], 200)
        reacted = self.request("GET", f"/api/conversations/{space_id}/messages", token=first_token)[1]["messages"][0]
        self.assertEqual(reacted["reactions"][0]["count"], 1)

    def test_only_server_admin_can_create_edit_or_manage_communities(self):
        admin, admin_token = self.register("server_admin", "Admin")
        member, member_token = self.register("regular_member", "Member")
        self.assertTrue(admin["is_admin"])
        self.assertFalse(member["is_admin"])

        status, created, _ = self.request("POST", "/api/communities", {"name": "First community"}, admin_token)
        self.assertEqual(status, 201)
        community = created["community"]
        community_id = community["id"]

        self.assertEqual(self.request("POST", "/api/communities", {"name": "Not allowed"}, member_token)[0], 403)
        self.assertEqual(
            self.request("PATCH", f"/api/communities/{community_id}", {"name": "Not allowed"}, member_token)[0],
            403,
        )
        self.assertEqual(
            self.request("POST", f"/api/communities/{community_id}/spaces", {"name": "private", "kind": "text"}, member_token)[0],
            403,
        )
        self.assertEqual(self.request("GET", f"/api/communities/{community_id}/invite", token=member_token)[0], 403)

        self.assertEqual(
            self.request("PATCH", f"/api/communities/{community_id}", {"name": "Renamed community"}, admin_token)[0],
            200,
        )
        community_list = self.request("GET", "/api/communities", token=admin_token)[1]["communities"]
        self.assertEqual(community_list[0]["name"], "Renamed community")

    def test_existing_database_assigns_oldest_account_as_server_admin(self):
        _, _ = self.register("legacy_admin", "Legacy Admin")
        _, member_token = self.register("legacy_member", "Legacy Member")
        with self.app.connect() as db:
            db.execute("DELETE FROM app_settings WHERE key = 'admin_user_id'")
        restarted = ComChat(self.app.database)
        self.assertTrue(restarted._session_user(self.login_token("legacy_admin", "a-unique-test-password"))["is_admin"])
        self.assertFalse(restarted._session_user(member_token)["is_admin"])

    def test_private_dm_is_shared_only_with_its_two_accounts(self):
        _, first_token = self.register("friend_one", "Friend One")
        _, second_token = self.register("friend_two", "Friend Two")
        _, outsider_token = self.register("outsider_1", "Outsider")
        status, result, _ = self.request("POST", "/api/dms", {"username": "FRIEND_TWO"}, first_token)
        self.assertEqual(status, 200)
        dm_id = result["dm"]["id"]
        self.assertEqual(self.request("POST", "/api/dms", {"username": "friend_one"}, second_token)[1]["dm"]["id"], dm_id)
        self.assertEqual(self.request("POST", f"/api/conversations/{dm_id}/messages", {"text": "Private hello"}, first_token)[0], 201)
        messages = self.request("GET", f"/api/conversations/{dm_id}/messages", token=second_token)[1]["messages"]
        self.assertEqual(messages[0]["text"], "Private hello")
        self.assertEqual(self.request("GET", f"/api/conversations/{dm_id}/messages", token=outsider_token)[0], 404)

    def test_members_cannot_read_or_write_unjoined_community_space(self):
        _, first_token = self.register("owner_one", "Owner")
        _, second_token = self.register("other_one", "Other")
        community = self.request("POST", "/api/communities", {"name": "Private"}, first_token)[1]["community"]
        space_id = community["spaces"][0]["id"]
        self.assertEqual(self.request("GET", f"/api/conversations/{space_id}/messages", token=second_token)[0], 404)
        self.assertEqual(self.request("POST", f"/api/conversations/{space_id}/messages", {"text": "Intrusion"}, second_token)[0], 404)

    def test_community_creation_only_creates_text_channels(self):
        _, token = self.register("text_user", "Text User")
        status, response, _ = self.request("POST", "/api/communities", {"name": "Text only"}, token)
        self.assertEqual(status, 201)
        self.assertEqual([space["kind"] for space in response["community"]["spaces"]], ["text"])
        self.assertEqual(
            self.request(
                "POST",
                f"/api/communities/{response['community']['id']}/spaces",
                {"name": "voice", "kind": "voice"},
                token,
            )[0],
            400,
        )

    def test_startup_removes_legacy_voice_rooms(self):
        _, token = self.register("legacy_voice", "Legacy Voice")
        community = self.request("POST", "/api/communities", {"name": "Old community"}, token)[1]["community"]
        with self.app.connect() as db:
            db.execute("PRAGMA ignore_check_constraints = ON")
            db.execute(
                "INSERT INTO spaces(id, community_id, name, kind, created_at) VALUES (?, ?, ?, ?, ?)",
                ("f" * 32, community["id"], "Hangout", "voice", 1),
            )
        restarted = ComChat(self.app.database)
        with restarted.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM spaces WHERE kind = 'voice'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
