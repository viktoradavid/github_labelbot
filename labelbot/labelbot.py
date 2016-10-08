#!/usr/bin/env python3

from posixpath import join as urljoin

import click
import json
import re
import requests
import sched
import sys
import time
import validators


class LabelBot(object):
    github_api_url = 'https://api.github.com'

    def __init__(self, token_file, rules_file, default_label, interval,
                 check_comments, recheck):
        self.last_issue_checked = 0
        self.scheduler = sched.scheduler(time.time, time.sleep)
        self.default_label = default_label
        self.interval = interval
        self.check_comments = check_comments
        self.recheck = recheck

        # get GitHub token
        self.token = self._get_token(token_file)

        # get request session and validate token
        self.session = self._get_requests_session(self.token)

        # load and validate rules
        self.rules = self._get_rules(rules_file)

        # TODO check status_code
        repos_endpoint = urljoin(self.github_api_url, 'user/repos')
        self.available_repos_json = self.session.get(repos_endpoint).json()

    def add_repos(self, repos):
        """Add repos and start labeling them"""
        # get list of user/repo values to be labeled
        repo_names = []
        for repo in repos:
            for available_repo in self.available_repos_json:
                found = False
                if repo == available_repo['html_url']:
                    repo_names.append(available_repo['full_name'])
                    found = True
            if not found:
                print('Repository {} is not valid or bot is not allowed to '
                      'access it. '.format(repo), file=sys.stderr)

        # start labeling issues in given repos
        for repo in repo_names:
            self.scheduler.enter(0, 1, self._label_issues,
                                 argument=(repo,))

    def run(self):
        """Initiate labeling by running a scheduler"""
        self.scheduler.run()

    def _label_issues(self, repo):
        """Add labels to issues in given repo based on labeling rules"""
        print("Labeling issues in " + repo)

        # TODO do not check all issues, only the new ones
        # (based on user options)

        # get issues in given repo
        issues_endpoint = urljoin(self.github_api_url, 'repos', repo, 'issues')
        response = self.session.get(issues_endpoint)
        try:
            response.raise_for_status()
        except:
            # TODO
            pass
        issues = response.json()

        # iterate through all isues
        for issue in issues:
            labels_to_add = []
            matched = False

            # match rules in issue body and title
            for rule in self.rules:
                if rule.pattern.findall(issue['body'])\
                        or rule.pattern.findall(issue['title']):
                    labels_to_add.append(rule.label)
                    matched = True

            # match rules in issue comments if needed
            issue_endpoint = urljoin(issues_endpoint, str(issue['number']))
            if self.check_comments:
                issue_comments_endpoint = urljoin(issue_endpoint, 'comments')
                response = self.session.get(issue_comments_endpoint)
                # TODO check status_code
                comments = response.json()
                for comment in comments:
                    for rule in self.rules:
                        if rule.pattern.findall(comment['body']):
                            labels_to_add.append(rule.label)
                            matched = True

            # get existing label strings
            existing_labels = [label['name'] for label in issue['labels']]

            # set default label if needed
            if self.default_label and matched == 0:
                labels_to_add.append(self.default_label)

            # set new labels
            labels_to_add = list(set(labels_to_add))  # make values unique
            new_labels = existing_labels + labels_to_add
            if not new_labels == existing_labels:
                response = self.session.patch(issue_endpoint,
                                              data=json.dumps(
                                                  {'labels': new_labels}))

        # run this again after given interval
        self.scheduler.enter(self.interval, 1, self._label_issues,
                             argument=(repo,))

    def _get_rules(self, rules_file):
        """Get labeling rules from the provided file"""
        # TODO improve rules validation
        rules = []
        with open(rules_file) as rules_file:
            for line in rules_file.readlines():
                words = line.splitlines()[0].split('::')
                if len(words) != 2:
                    print("Skipping invalid rule. ", file=sys.stderr)
                    continue
                rules.append(LabelingRule(words[0], words[1]))
        return rules

    def _get_token(self, token_file):
        """Get GitHub token from the provided file"""
        with open(token_file) as token_file:
            token = token_file.readline().splitlines()[0]

        return token

    def _get_requests_session(self, token):
        """Returns a requests session and verifies valid GitHub token was
        provided"""
        session = requests.Session()
        session.headers = {'Authorization': 'token ' + token,
                           'User-Agent': 'Python'}
        try:
            response = session.get('https://api.github.com/user')
        except:
            print('Could not connect to GitHub. Are you online? ',
                  file=sys.stderr)
            sys.exit(1)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            print('Couldn\'t connect to GitHub: ' +
                  str(response.status_code) +
                  ' - ' +
                  response.reason,
                  file=sys.stderr)

            if response.status_code == 401:
                print('Did you provide a valid token? ',
                      file=sys.stderr)

            sys.exit(1)

        return session


class UrlParam(click.ParamType):
    """Class used for validating GitHub Repository URLs"""
    name = 'url'

    def convert(self, value, param, ctx):
        if not validators.url(value):
            self.fail('{} is not a valid URL. '.format(value), param, ctx)

        if 'github.com' not in value:
            self.fail('{} is not a GitHub URL. '.format(value), param, ctx)

        try:
            response = requests.get(value)
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            self.fail('{} is not accessible. '.format(value), param, ctx)

        return value


class LabelingRule(object):
    """Simple structure holding a single labeling rule"""
    def __init__(self, regex, label):
        self.pattern = re.compile(regex)
        self.label = label
