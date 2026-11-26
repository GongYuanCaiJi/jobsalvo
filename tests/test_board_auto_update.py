# -*- coding: utf-8 -*-
"""更新從看板伺服器入口回報限制，並由伺服器排程而非頁面發起。"""
import json
import os
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.test_board import HttpBase
import board_server as bs
import config as cf
import update
import board_doc as bd

NO_HOOKS = {'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':'/dev/null',
            'GIT_AUTHOR_NAME':'test','GIT_AUTHOR_EMAIL':'test@example.test',
            'GIT_COMMITTER_NAME':'test','GIT_COMMITTER_EMAIL':'test@example.test'}


def git(cwd,*args):
    subprocess.run(['git','-C',cwd,*args],check=True,capture_output=True,env=dict(os.environ,**NO_HOOKS))


class BoardAutoUpdate(HttpBase):
    def test_copy_reports_that_it_does_not_check_or_update(self):
        code, body, _ = self.req('/api/update')
        result = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(result['blocked'], '副本不檢查更新')
        self.assertFalse(result['auto'])

    def test_page_and_revision_endpoint_share_the_server_version(self):
        page = bd._parse(self.req('/')[1].decode('utf-8'))['data']
        revision = json.loads(self.req('/api/rev')[1])
        self.assertEqual(page['server_version'], revision['server_version'])

    def test_manual_update_and_new_work_report_that_an_update_is_in_progress(self):
        with mock.patch.object(bs, 'is_real', return_value=True), \
                mock.patch.object(bs, '_UPDATE_IN_PROGRESS', True):
            code, body, _ = self.req('/api/update', method='POST', data=b'{}')
            self.assertEqual(code, 409)
            self.assertIn('正在更新', json.loads(body)['msg'])
            code, body, _ = self.req('/api/run/prep', body={})
            self.assertEqual(code, 409)
            self.assertIn('正在更新', json.loads(body)['msg'])

    def test_update_button_reports_an_active_background_rebuild(self):
        with tempfile.TemporaryDirectory(prefix='update-button-busy-') as tmp, \
                mock.patch.object(bs, 'is_real', return_value=True), \
                mock.patch.object(cf, 'TMP', tmp), \
                mock.patch.dict(bs._build_state, {'running':True}):
            code, body, _ = self.req('/api/update', method='POST', data=b'{}')
        self.assertEqual(code, 409)
        self.assertIn('背景重建正在跑', json.loads(body)['msg'])

    def test_no_new_version_keeps_the_last_successful_automatic_update(self):
        with tempfile.TemporaryDirectory(prefix='auto-update-status-') as tmp, \
                mock.patch.object(bs, 'is_real', return_value=True), \
                mock.patch.object(bs, '_update_busy_reason', return_value=''), \
                mock.patch.object(cf, 'TMP', tmp), \
                mock.patch.dict(os.environ, {'JOBSALVO_LAUNCHD':'1'}):
            previous={'at':123,'version':'old-success','msg':'更新到 old-success 了'}
            bs._write_auto_update_status(previous)
            with mock.patch.object(update, 'check', return_value={'new':False,'current':'current','latest':'current','blocked':''}):
                result=bs._perform_update(automatic=True,cwd='/unused')
                code, body, _ = self.req('/api/update')
            self.assertTrue(result['ok'])
            self.assertEqual(code,200)
            self.assertEqual(json.loads(body)['auto_status'],previous)

    def test_successful_update_starts_a_rebuild_deferred_during_the_update(self):
        started=threading.Event()
        release=threading.Event()
        finished=threading.Event()
        def apply(_cwd):
            bs._BUILD_AFTER_UPDATE=True
            return {'ok':True,'version':'new','msg':'更新到 new 了'}
        def rebuild(*_args,**_kwargs):
            started.set()
            release.wait(5)
            return subprocess.CompletedProcess([],0)
        old_live=bd.LIVE
        old_state=dict(bs._build_state)
        with tempfile.TemporaryDirectory(prefix='auto-update-rebuild-') as tmp, \
                mock.patch.object(bs, 'is_real', return_value=True), \
                mock.patch.object(bs, '_update_busy_reason', return_value=''), \
                mock.patch.object(cf, 'TMP', tmp), \
                mock.patch.object(bd, 'LIVE', self.path), \
                mock.patch.object(bs, 'STATE', self.path), \
                mock.patch.object(bs, 'PILOT', mock.Mock(kick=finished.set)), \
                mock.patch.object(update, 'check', return_value={'new':True,'current':'old','latest':'new','blocked':''}), \
                mock.patch.object(update, 'apply', side_effect=apply), \
                mock.patch.object(bs.subprocess, 'run', side_effect=rebuild), \
                mock.patch.dict(os.environ, {'JOBSALVO_LAUNCHD':'1'}):
            bs._BUILD_AFTER_UPDATE=False
            bs._build_state.update(running=False,again=False)
            try:
                result=bs._perform_update(automatic=True,cwd='/unused')
                marker=bs._deferred_build_path()
                self.assertTrue(os.path.isfile(marker))
                bs.trigger_build=self._tb
                bs._resume_deferred_build()
                self.assertTrue(started.wait(5),'重建子行程沒有啟動')
                code, body, _ = self.req('/api/rev')
                self.assertEqual(code,200)
                self.assertTrue(json.loads(body)['building'])
                self.assertFalse(os.path.exists(marker))
            finally:
                release.set()
                finished.wait(5)
                bd.LIVE=old_live
                bs._build_state.clear(); bs._build_state.update(old_state)
        self.assertTrue(result['ok'])

    def test_server_does_not_handoff_the_new_code_until_update_finishes(self):
        stopped=threading.Event()
        class Server:
            def shutdown(self): stopped.set()
        with mock.patch.object(bs, '_UPDATE_IN_PROGRESS', True):
            watcher=threading.Thread(target=bs._watch,args=([Server()],'before-update',True,0.01),daemon=True)
            watcher.start()
            self.assertFalse(stopped.wait(0.2))
            bs._UPDATE_IN_PROGRESS=False
            self.assertTrue(stopped.wait(3))
            watcher.join(timeout=1)

    def _watch_after_manual_update(self, applied):
        """按鈕更新,update.apply 回 applied;回 (看板有沒有讓位, 設定頁讀到的更新紀錄)。版本值沒變(像只改了套件清單)。"""
        stopped=threading.Event()
        class Server:
            def shutdown(self): stopped.set()
        self.addCleanup(setattr, bs, '_RESTART_AFTER_UPDATE', False)
        with tempfile.TemporaryDirectory(prefix='update-restart-') as tmp, \
                mock.patch.object(bs, 'is_real', return_value=True), \
                mock.patch.object(bs, '_update_busy_reason', return_value=''), \
                mock.patch.object(cf, 'TMP', tmp), \
                mock.patch.object(update, 'current', return_value='new-version'), \
                mock.patch.object(update, 'apply', return_value=applied):
            watcher=threading.Thread(target=bs._watch,args=([Server()],bs.code_version(),True,0.01),daemon=True)
            watcher.start()
            bs._perform_update(cwd='/unused')
            return stopped.wait(1), bs._read_auto_update_status()

    def test_a_successful_update_restarts_even_when_only_the_package_list_changed(self):
        restarted, status = self._watch_after_manual_update({'ok':True,'version':'new-version','msg':'更新好了'})
        self.assertTrue(restarted)
        self.assertEqual(status['version'], 'new-version')

    def test_a_failed_button_update_keeps_running_and_shows_why_in_settings(self):
        restarted, status = self._watch_after_manual_update({'ok':False,'msg':'套件裝不起來,已退回原本那一版'})
        self.assertFalse(restarted)
        self.assertEqual(status['failure']['msg'], '套件裝不起來,已退回原本那一版')

    def test_server_updates_from_its_hourly_loop_without_a_page_request(self):
        with tempfile.TemporaryDirectory(prefix='server-auto-update-') as root:
            origin, work, app = (os.path.join(root, name) for name in ('origin.git','work','app'))
            env=dict(os.environ,**NO_HOOKS)
            subprocess.run(['git','init','-q','--bare','-b','main',origin],check=True,env=env)
            subprocess.run(['git','init','-q','-b','main',work],check=True,env=env)
            git(work,'remote','add','origin','../origin.git')
            with open(os.path.join(work,'release.txt'),'w',encoding='utf-8') as output: output.write('old')
            git(work,'add','release.txt'); git(work,'commit','-q','-m','old'); git(work,'push','-q','origin','main')
            subprocess.run(['git','clone','-q',origin,app],check=True,env=env)
            git(app,'remote','set-url','origin','../origin.git')
            with open(os.path.join(work,'release.txt'),'w',encoding='utf-8') as output: output.write('new')
            git(work,'commit','-q','-am','new'); git(work,'push','-q','origin','main')
            fake_uv=os.path.join(root,'uv')
            with open(fake_uv,'w',encoding='utf-8') as output: output.write('#!/bin/sh\nexit 0\n')
            os.chmod(fake_uv,0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions  暫存夾裡的假指令要能執行
            stop=threading.Event()
            worker=None
            job=None
            old_state=bs.STATE
            try:
                bs.STATE=cf.LIVE
                with mock.patch.object(cf,'TMP',os.path.join(root,'server-tmp')), \
                        mock.patch.dict(cf.C['updates'],{'auto':False}), \
                        mock.patch.dict(os.environ,{'JOBSALVO_LAUNCHD':'1'}):
                    skipped=bs._perform_update(automatic=True,cwd=app)
                    self.assertTrue(skipped.get('skipped'))
                    with open(os.path.join(app,'release.txt'),encoding='utf-8') as source:
                        self.assertEqual(source.read(),'old')
                with mock.patch.object(cf,'TMP',os.path.join(root,'server-tmp')), \
                        mock.patch.object(bs,'is_real',return_value=True), \
                        mock.patch.object(update.shutil,'which',return_value=fake_uv), \
                        mock.patch.object(update.shutil,'rmtree'), \
                        mock.patch.dict(os.environ,{'JOBSALVO_LAUNCHD':'1'}):
                    os.makedirs(os.path.join(root,'server-tmp'),exist_ok=True)
                    job=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)','cut_tailor.py'],
                                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    import jobrun
                    jobrun.write(os.path.join(root,'server-tmp','cut_tailor_status.json'),
                                 {'phase':'start','pid':job.pid,'t0':time.time()})
                    worker=threading.Thread(target=bs._automatic_update_loop,args=(stop,0.01,app),daemon=True)
                    worker.start()
                    time.sleep(0.3)
                    with open(os.path.join(app,'release.txt'),encoding='utf-8') as source:
                        self.assertEqual(source.read(),'old')
                    job.terminate(); job.wait(timeout=5); job=None
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        with open(os.path.join(app,'release.txt'),encoding='utf-8') as source:
                            if source.read()=='new': break
                        time.sleep(0.02)
                    stop.set(); worker.join(timeout=2)
                with open(os.path.join(app,'release.txt'),encoding='utf-8') as source:
                    self.assertEqual(source.read(),'new')
                with mock.patch.object(cf,'TMP',os.path.join(root,'server-tmp')):
                    self.assertTrue(bs._read_auto_update_status().get('at'))
            finally:
                stop.set()
                if worker: worker.join(timeout=2)
                if job:
                    job.terminate(); job.wait(timeout=5)
                bs.STATE=old_state


if __name__ == '__main__':
    unittest.main()
