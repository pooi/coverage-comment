#!/usr/bin/env python3

import re
import sys
from xml.etree.ElementTree import parse

import requests

changed_file_target_type = ["INSTRUCTION", "LINE", "METHOD"]
target_type = ["INSTRUCTION", "LINE", "METHOD", "CLASS"]
comment_marker = "<!-- coverage-comment -->"
legacy_comment_signature = "## Total Test Coverage:\n|Type|Coverage|\n|---|---|"


def find_pull_request():
    response = requests.get(
        f"{github_api_url}/repos/{repository}/pulls",
        headers=api_headers
    )

    if response.ok:
        pull_requests = response.json()
        for pull_request in pull_requests:
            if pull_request["head"]["ref"] == branch:
                return pull_request["url"]
        return None
    else:
        print(f"FIND-PULL-REQUEST-ERROR, code={response.status_code}, body={response.json()}")
        return None


def get_pull_request_files():
    response = requests.get(
        f"{pull_request_url}/files?per_page=100",
        headers=api_headers
    )

    if response.ok:
        return response.json()
    else:
        print(f"GET-PULL-REQUEST-FILES-ERROR, code={response.status_code}, body={response.json()}")
        return []


def get_pull_request_comments():
    comments = []
    page = 1
    comments_url = f"{pull_request_url}/comments".replace("/pulls/", "/issues/")

    while True:
        response = requests.get(
            comments_url,
            headers=api_headers,
            params={"per_page": 100, "page": page}
        )
        if not response.ok:
            raise RuntimeError(
                f"GET-REVIEW-COMMENTS-ERROR, code={response.status_code}, body={response.text}"
            )

        page_comments = response.json()
        comments.extend(page_comments)
        if len(page_comments) < 100:
            return comments
        page += 1


def get_authenticated_login():
    response = requests.get(
        f"{github_api_url}/user",
        headers=api_headers
    )
    if response.ok:
        return response.json()["login"]

    # GITHUB_TOKEN is an installation token, so the user endpoint may be
    # unavailable even though comments are written by github-actions[bot].
    if response.status_code == 403:
        return None

    raise RuntimeError(
        f"GET-AUTHENTICATED-USER-ERROR, code={response.status_code}, body={response.text}"
    )


def find_review_comment(comments):
    marked_comments = [
        comment for comment in comments
        if comment_marker in (comment.get("body") or "")
    ]
    if marked_comments:
        return max(marked_comments, key=lambda comment: comment["id"])

    legacy_comments = [
        comment for comment in comments
        if legacy_comment_signature in (comment.get("body") or "")
    ]
    if not legacy_comments:
        return None

    authenticated_login = get_authenticated_login()
    expected_login = authenticated_login or "github-actions[bot]"
    own_legacy_comments = [
        comment for comment in legacy_comments
        if comment.get("user", {}).get("login") == expected_login
    ]
    if not own_legacy_comments:
        return None
    return max(own_legacy_comments, key=lambda comment: comment["id"])


def add_comment_marker(comment):
    body = comment.replace(comment_marker, "").lstrip()
    return f"{comment_marker}\n{body}"


def create_review_comment(comment):
    response = requests.post(
        f"{pull_request_url}/comments".replace("/pulls/", "/issues/"),
        headers=api_headers,
        json={
            'body': comment
        }
    )

    if response.ok:
        return response.json()
    raise RuntimeError(
        f"CREATE-REVIEW-COMMENT-ERROR, code={response.status_code}, body={response.text}"
    )


def update_review_comment(review_comment, comment):
    response = requests.patch(
        review_comment["url"],
        headers=api_headers,
        json={
            "body": comment
        }
    )

    if response.ok:
        return response.json()
    raise RuntimeError(
        f"UPDATE-REVIEW-COMMENT-ERROR, code={response.status_code}, body={response.text}"
    )


def upsert_review_comment(comment):
    marked_comment = add_comment_marker(comment)
    review_comment = find_review_comment(get_pull_request_comments())
    if review_comment is None:
        return create_review_comment(marked_comment)
    return update_review_comment(review_comment, marked_comment)


def calc_coverage(covered, missed):
    if float(missed) + float(covered) == 0.0:
        return "none"
    return f"{round(float(covered) / (float(missed) + float(covered)) * 100, 2)}% " \
           f"({int(covered)}/{int(covered) + int(missed)})"


def generate_changed_files_table(changed_files_coverage):
    is_multiple = len(changed_files_coverage) > 1
    text = f"## Changed File{'s' if is_multiple else ''} Coverage:\n"
    headers = ['File name'] + changed_file_target_type
    text += f"|{'|'.join(headers)}|\n"
    text += f"|{'|'.join(['---' for _ in headers])}|\n"

    coverage_summary = {coverage_type: {"covered": 0.0, "missed": 0.0} for coverage_type in changed_file_target_type}
    for file_name, data in changed_files_coverage.items():

        for coverage_type in changed_file_target_type:
            coverage_summary[coverage_type]["covered"] += data[coverage_type]["covered"]
            coverage_summary[coverage_type]["missed"] += data[coverage_type]["missed"]

        coverages = [data[coverage_type]["coverage"] for coverage_type in changed_file_target_type]
        text += f"|{'|'.join([file_name] + coverages)}|\n"

    if len(changed_files_coverage.items()) > 1:
        coverages = [
            f"**{calc_coverage(coverage_summary[coverage_type]['covered'], coverage_summary[coverage_type]['missed'])}**"
            for coverage_type in changed_file_target_type]
        text += f"|{'|'.join(['**Summary**'] + coverages)}|\n"
        print(coverage_summary)
    return text


def generate_table(name, coverage_map):
    text = f"# {name}\n"
    text += f"## Total Test Coverage:\n"
    text += "|Type|Coverage|\n"
    text += "|---|---|\n"
    for coverage_type in coverage_map:
        text += f"|{coverage_type}|{coverage_map[coverage_type]}|\n"
    return text


def get_changed_files():
    return [file['filename'] for file in get_pull_request_files()]


def build_changed_files_coverage(root):
    changed_files = get_changed_files()
    print("\n".join(changed_files))

    converted_changed_files = set()
    changed_files_coverage = {}
    for changed_file in changed_files:
        file_name = None
        for language, extension in [("java", ".java"), ("kotlin", ".kt"), ("groovy", ".groovy")]:
            if changed_file.endswith(extension) and f"main/{language}" in changed_file:
                file_name = changed_file[
                            re.search(f"main/{language}", changed_file).end() + 1:]

        if file_name is not None and file_name not in converted_changed_files:
            converted_changed_files.add(file_name)

    for package in root.findall("package"):
        for cls in package.findall("class"):
            class_name = cls.attrib['name']
            class_source_file_name = cls.attrib['sourcefilename']
            for file in converted_changed_files:
                file_name, source_file_name = file.rsplit("/", 1)
                if class_name.startswith(file_name) and source_file_name == class_source_file_name:

                    if file in changed_files_coverage:
                        data = changed_files_coverage[file]
                    else:
                        data = {t: {"covered": 0.0, "missed": 0.0} for t in changed_file_target_type}
                        changed_files_coverage[file] = data

                    for counter in cls.findall("counter"):
                        counter_type = counter.attrib["type"]
                        if counter_type in changed_file_target_type:
                            data[counter_type]["covered"] += float(counter.attrib["covered"])
                            data[counter_type]["missed"] += float(counter.attrib["missed"])

    for file_name, data in changed_files_coverage.items():
        for coverage_data in data.values():
            coverage_data["coverage"] = calc_coverage(coverage_data["covered"], coverage_data["missed"])

    return changed_files_coverage


def build_total_coverage(root):
    return {
        counter.attrib['type']: calc_coverage(counter.attrib['covered'], counter.attrib['missed'])
        for counter in root.findall("counter")
        if counter.attrib['type'] in target_type
    }


def main():
    comment = ""
    for i, xml_path in enumerate(xml_paths):
        try:
            tree = parse(xml_path)
            root = tree.getroot()
        except:
            continue

        coverage = build_total_coverage(root)
        try:
            changed_files_coverage = build_changed_files_coverage(root)
        except Exception as e:
            print("build_changed_files_coverage-error,", e)
            changed_files_coverage = {}

        name = xml_path.split("/")[0]
        comment += generate_table(name, coverage)
        if changed_files_coverage:
            comment += "\n<br>\n\n"
            comment += generate_changed_files_table(changed_files_coverage)

        if i + 1 < len(xml_paths):
            comment += "\n<br>\n\n---\n\n<br>\n\n"

    print(comment)
    upsert_review_comment(comment)


if __name__ == '__main__':
    if len(sys.argv) > 5:
        xml_paths = [p.strip() for p in sys.argv[1].split(",")]
        github_token = sys.argv[2]

        api_headers = {
            'Accept': 'application/vnd.github.v3+json',
            'Authorization': f"token {github_token}"
        }

        github_api_url = sys.argv[3]
        repository = sys.argv[4]
        branch = sys.argv[5]
        branch = branch.split("/")[-1]

        if len(sys.argv) > 6:
            pull_request_url = sys.argv[6]
        else:
            pull_request_url = find_pull_request()

        if pull_request_url is None:
            print("CAN'T FIND PULL REQUEST")
            exit(0)
        else:
            print(pull_request_url)

        main()
    else:
        print("Invalid arguments")
