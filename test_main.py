import unittest
from unittest.mock import patch

import main


class FakeResponse:
    def __init__(self, data=None, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self):
        return self._data


class ReviewCommentTest(unittest.TestCase):
    def setUp(self):
        main.github_api_url = "https://api.github.test"
        main.pull_request_url = "https://api.github.test/repos/owner/repo/pulls/7"
        main.api_headers = {"Authorization": "token test"}

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_updates_latest_marked_comment(self, get, post, patch_request):
        get.return_value = FakeResponse([
            self.comment(1, main.comment_marker),
            self.comment(3, main.comment_marker),
            self.comment(2, "unrelated")
        ])
        patch_request.return_value = FakeResponse({"id": 3})

        main.upsert_review_comment("coverage")

        patch_request.assert_called_once_with(
            "https://api.github.test/comments/3",
            headers=main.api_headers,
            json={"body": f"{main.comment_marker}\ncoverage"}
        )
        post.assert_not_called()

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_adopts_latest_legacy_comment_from_same_author(self, get, post, patch_request):
        legacy_body = main.legacy_comment_signature
        get.side_effect = [
            FakeResponse([
                self.comment(2, legacy_body, "coverage-bot"),
                self.comment(4, legacy_body, "someone-else"),
                self.comment(3, legacy_body, "coverage-bot")
            ]),
            FakeResponse({"login": "coverage-bot"})
        ]
        patch_request.return_value = FakeResponse({"id": 3})

        main.upsert_review_comment("coverage")

        self.assertEqual(patch_request.call_args.args[0], "https://api.github.test/comments/3")
        post.assert_not_called()

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_adopts_github_actions_legacy_comment_for_installation_token(
            self, get, post, patch_request):
        get.side_effect = [
            FakeResponse([self.comment(2, main.legacy_comment_signature)]),
            FakeResponse(status_code=403, text="forbidden")
        ]
        patch_request.return_value = FakeResponse({"id": 2})

        main.upsert_review_comment("coverage")

        self.assertEqual(patch_request.call_args.args[0], "https://api.github.test/comments/2")
        post.assert_not_called()

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_creates_comment_when_no_match_exists(self, get, post, patch_request):
        get.return_value = FakeResponse([])
        post.return_value = FakeResponse({"id": 1}, status_code=201)

        main.upsert_review_comment(f"{main.comment_marker}\ncoverage")

        post.assert_called_once_with(
            "https://api.github.test/repos/owner/repo/issues/7/comments",
            headers=main.api_headers,
            json={"body": f"{main.comment_marker}\ncoverage"}
        )
        patch_request.assert_not_called()
        self.assertEqual(post.call_args.kwargs["json"]["body"].count(main.comment_marker), 1)

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_follows_comment_pagination(self, get, post, patch_request):
        first_page = [self.comment(i, "unrelated") for i in range(1, 101)]
        get.side_effect = [
            FakeResponse(first_page),
            FakeResponse([self.comment(101, main.comment_marker)])
        ]
        patch_request.return_value = FakeResponse({"id": 101})

        main.upsert_review_comment("coverage")

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page"], 2)
        self.assertEqual(patch_request.call_args.args[0], "https://api.github.test/comments/101")
        post.assert_not_called()

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_does_not_create_when_comment_listing_fails(self, get, post, patch_request):
        get.return_value = FakeResponse(status_code=500, text="server error")

        with self.assertRaises(RuntimeError):
            main.upsert_review_comment("coverage")

        post.assert_not_called()
        patch_request.assert_not_called()

    @patch("main.requests.patch")
    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_does_not_create_when_update_fails(self, get, post, patch_request):
        get.return_value = FakeResponse([self.comment(1, main.comment_marker)])
        patch_request.return_value = FakeResponse(status_code=500, text="server error")

        with self.assertRaises(RuntimeError):
            main.upsert_review_comment("coverage")

        post.assert_not_called()

    @staticmethod
    def comment(comment_id, body, login="github-actions[bot]"):
        return {
            "id": comment_id,
            "url": f"https://api.github.test/comments/{comment_id}",
            "body": body,
            "user": {"login": login}
        }


if __name__ == "__main__":
    unittest.main()
