"""Tests for GUI update in Updater.git_install(), running real git in temporary repositories."""
import datetime
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch

from deploy.config import ExecutionError
from deploy.git import GitManager
from module.webui.fake_pil_module import remove_fake_pil_module
from module.webui.updater import Updater, updater

# Importing webui leaves a fake PIL behind, which breaks other tests in this process
remove_fake_pil_module()


def git(cwd, *args):
    return subprocess.run(
        [updater.git, '-c', 'user.name=test', '-c', 'user.email=test@test', *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding='utf8',
    ).stdout.strip()


def write(cwd, file, text):
    with open(os.path.join(cwd, file), 'w', encoding='utf8') as f:
        f.write(text)


def read(cwd, file):
    with open(os.path.join(cwd, file), encoding='utf8') as f:
        return f.read()


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.remote = os.path.join(self.root, 'remote')
        self.local = os.path.join(self.root, 'local')
        os.mkdir(self.remote)
        git(self.remote, 'init')
        git(self.remote, 'symbolic-ref', 'HEAD', 'refs/heads/master')
        write(self.remote, 'a.txt', 'a1\n')
        write(self.remote, 'b.txt', 'b1\n')
        git(self.remote, 'add', '.')
        git(self.remote, 'commit', '-m', 'first')
        git(self.root, 'clone', self.remote, self.local)
        git(self.local, 'config', 'user.name', 'test')
        git(self.local, 'config', 'user.email', 'test@test')

        write(self.remote, 'a.txt', 'a2\n')
        git(self.remote, 'commit', '-am', 'second')
        self.remote_head = git(self.remote, 'rev-parse', 'HEAD')

        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.local)

        self.updater = self.build_updater()
        patcher = patch('module.base.retry.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_updater(self):
        instance = Updater.__new__(Updater)
        # Bypass DeployConfig.__setattr__() which writes the real deploy.yaml
        values = dict(
            config={}, Repository=self.remote, Branch='master', GitProxy=None,
            SSLVerify=True, AutoUpdate=False, git=updater.git,
        )
        for key, value in values.items():
            object.__setattr__(instance, key, value)
        return instance

    def head(self):
        return git(self.local, 'rev-parse', 'HEAD')

    def test_update_with_auto_update_disabled(self):
        self.updater.git_install()
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual(read(self.local, 'a.txt'), 'a2\n')

    def test_git_config_untouched(self):
        git(self.local, 'remote', 'add', 'upstream', self.remote)
        before = git(self.local, 'config', '--local', '--list')
        self.updater.git_install()
        self.assertEqual(git(self.local, 'config', '--local', '--list'), before)

    def test_lock_file_fails_without_removing(self):
        lock = os.path.join(self.local, '.git', 'index.lock')
        write(self.local, '.git/index.lock', '')
        with self.assertRaises(ExecutionError):
            self.updater.git_install()
        self.assertTrue(os.path.exists(lock))

    def test_keep_unrelated_local_change(self):
        write(self.local, 'b.txt', 'local\n')
        self.updater.git_install()
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual(read(self.local, 'b.txt'), 'local\n')

    def test_conflict_local_change_fails_without_discarding(self):
        write(self.local, 'a.txt', 'local\n')
        head = self.head()
        with self.assertRaises(ExecutionError):
            self.updater.git_install()
        self.assertEqual(self.head(), head)
        self.assertEqual(read(self.local, 'a.txt'), 'local\n')

    def test_conflict_reason_logged_and_not_retried(self):
        write(self.local, 'a.txt', 'local\n')
        with patch('module.webui.updater.logger') as logger, \
                patch.object(Updater, 'execute', wraps=self.updater.execute) as execute:
            with self.assertRaises(ExecutionError):
                self.updater.git_install()
        self.assertEqual(execute.call_count, 2)
        self.assertTrue(any('a.txt' in str(call) for call in logger.info.call_args_list))

    def test_local_commit_fails_without_discarding(self):
        write(self.local, 'b.txt', 'committed\n')
        git(self.local, 'commit', '-am', 'local')
        head = self.head()
        with self.assertRaises(ExecutionError):
            self.updater.git_install()
        self.assertEqual(self.head(), head)

    def test_other_branch_fails_without_merging(self):
        git(self.local, 'checkout', '-b', 'feature')
        head = self.head()
        with self.assertRaises(ExecutionError):
            self.updater.git_install()
        self.assertEqual(self.head(), head)

    def test_other_branch_reports_no_update(self):
        git(self.local, 'checkout', '-b', 'feature')
        with patch.object(Updater, 'execute') as execute:
            self.assertFalse(self.updater._check_update())
        execute.assert_not_called()

    def test_fetch_failure_retried(self):
        object.__setattr__(self.updater, 'Branch', 'missing')
        git(self.local, 'checkout', '-b', 'missing')
        with patch.object(Updater, 'execute', wraps=self.updater.execute) as execute:
            with self.assertRaises(ExecutionError):
                self.updater.git_install()
        self.assertEqual(execute.call_count, 3)

    def test_git_args_from_deploy_config(self):
        self.assertEqual(self.updater.git_args, '')
        object.__setattr__(self.updater, 'GitProxy', 'http://127.0.0.1:7890')
        object.__setattr__(self.updater, 'SSLVerify', False)
        self.assertEqual(
            self.updater.git_args,
            ' -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890'
            ' -c http.sslVerify=false')

    def test_git_over_cdn_fetch_keeps_local_change(self):
        object.__setattr__(self.updater, 'GitOverCdn', True)
        client = Mock()
        client.update_refs.side_effect = lambda: git(self.local, 'fetch', 'origin', 'master') is not None
        write(self.local, 'b.txt', 'local\n')
        with patch.object(Updater, 'goc_client', new=client):
            self.updater.git_install()
        client.update.assert_not_called()
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual(read(self.local, 'b.txt'), 'local\n')

    def test_git_over_cdn_failed_fallback_to_git(self):
        object.__setattr__(self.updater, 'GitOverCdn', True)
        client = Mock()
        client.download_pack.return_value = False
        with patch.object(Updater, 'goc_client', new=client):
            self.updater.git_install()
        self.assertEqual(self.head(), self.remote_head)

    def test_auto_update_keeps_reset_behavior(self):
        object.__setattr__(self.updater, 'AutoUpdate', True)
        write(self.local, 'a.txt', 'local\n')
        self.updater.git_install()
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual(read(self.local, 'a.txt'), 'a2\n')

    def test_startup_install_still_follows_auto_update(self):
        head = self.head()
        GitManager.git_install(self.updater)
        self.assertEqual(self.head(), head)

    def run_schedule_once(self):
        object.__setattr__(self.updater, 'state', 1)
        schedule_time = PropertyMock(return_value=datetime.time(3, 50))
        with patch.object(Updater, 'schedule_time', schedule_time), \
                patch.object(Updater, 'read'), \
                patch.object(Updater, 'check_update') as self.check_update, \
                patch.object(Updater, 'run_update') as run_update, \
                patch('module.webui.updater.State') as state:
            state.restart_event = Mock()
            schedule = self.updater.schedule_update()
            next(schedule)
            schedule.send(Mock())
            next(schedule)
        return run_update

    def test_schedule_skip_when_auto_update_disabled(self):
        self.run_schedule_once().assert_not_called()
        self.check_update.assert_not_called()

    def test_schedule_update_when_auto_update_enabled(self):
        object.__setattr__(self.updater, 'AutoUpdate', True)
        self.run_schedule_once().assert_called_once()


if __name__ == '__main__':
    unittest.main()
