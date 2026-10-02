import unittest
from build.ci.release_route import route


class ReleaseRouteTests(unittest.TestCase):
    def request(self, channel='dev', purpose=None):
        purpose = purpose or ('prd' if channel == 'stable' else 'dev')
        return dict(schema='libreecho-release-request-v1', channel=channel,
                    purpose=purpose, publish=purpose != 'sandbox',
                    version='', release_tag='', release_notes='')

    def test_manual_release_dev_is_not_stable(self):
        self.assertEqual(route(self.request(), 'release/0.13.11', 'workflow_dispatch'), 'dev')

    def test_release_dev_push_fails_closed(self):
        with self.assertRaises(ValueError):
            route(self.request(), 'release/0.13.11', 'push')

    def test_sandbox_never_publishes(self):
        for branch, event in [('main', 'push'), ('main', 'workflow_dispatch'),
                              ('release/0.13.11', 'workflow_dispatch'),
                              ('release/0.13.11', 'push'), ('main', 'pull_request')]:
            with self.subTest(branch=branch, event=event):
                self.assertEqual(route(self.request(purpose='sandbox'), branch, event), 'none')

    def test_missing_purpose_fails_closed(self):
        request = self.request()
        del request['purpose']
        with self.assertRaises(ValueError):
            route(request, 'main', 'push')

    def test_purpose_channel_mismatch_fails_closed(self):
        for purpose, channel in [('dev', 'stable'), ('prd', 'dev')]:
            with self.subTest(purpose=purpose), self.assertRaises(ValueError):
                route(self.request(channel, purpose), 'main', 'workflow_dispatch')

    def test_sandbox_refuses_publication_before_channel_or_event_logic(self):
        request = self.request(purpose='sandbox') | {'channel': 'stable', 'publish': True}
        self.assertEqual(route(request, 'untrusted/ref', 'untrusted-event'), 'none')

    def test_main_dev_events(self):
        for event in ('push', 'schedule', 'workflow_dispatch'):
            self.assertEqual(route(self.request(), 'main', event), 'dev')

    def test_prd_retains_required_fields(self):
        request = self.request('stable')
        with self.assertRaises(ValueError):
            route(request, 'release/0.13.11', 'workflow_dispatch')
        request.update(version='0.13.11', release_tag='radar-puffin-v0.13.11',
                       release_notes='release/notes.md', amonet_repository='reviewed',
                       amonet_tag='reviewed', amonet_commit='a'*40, ssh_enabled='0')
        self.assertEqual(route(request, 'release/0.13.11', 'workflow_dispatch'), 'stable')
        for branch, event in [('main', 'workflow_dispatch'), ('release/0.13.11', 'push'),
                              ('release/0.13.12', 'workflow_dispatch')]:
            with self.assertRaises(ValueError):
                route(request, branch, event)

    def test_invalid_request_rejected(self):
        for request in (None, {}, self.request('unknown'), self.request() | {'version': '0.13.11'},
                        self.request(purpose='unknown')):
            with self.assertRaises(ValueError):
                route(request, 'main', 'push')

    def test_untrusted_event_and_branch_rejected(self):
        for branch, event in [('fix/example', 'workflow_dispatch'), ('main', 'pull_request')]:
            with self.assertRaises(ValueError):
                route(self.request(), branch, event)
