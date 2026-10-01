# -*- coding: utf-8 -*-
"""看板的「⏹ 停止」:會收工的流程第一次按只停底下的 agent、主程式留著收工;收工中再按一次才整串停掉。"""
import os, sys, subprocess, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import jobrun  # noqa: E402

MARK = 'jobrun_stop_test_parent'
# 假的「主程式」:開一個子行程當 agent,自己一直活著
PARENT = ("import subprocess,sys,time  # " + MARK + "\n"
          "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
          "print(child.pid,flush=True)\n"
          "time.sleep(60)\n")


def alive(pid):
    r = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return bool(r.stdout.strip()) and not r.stdout.strip().startswith('Z')


class GracefulStop(unittest.TestCase):
    def test_first_stop_only_stops_the_agent_second_stops_everything(self):
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='jobrun-test-'))
        path = os.path.join(d, 'status.json')
        parent = subprocess.Popen([sys.executable, '-c', PARENT], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        child = int(parent.stdout.readline())
        jobrun.write(path, {'phase': 'judge', 'pid': parent.pid, 'graceful': True})

        ok, msg = jobrun.control(path, (MARK,), 'stop')
        self.assertTrue(ok)
        self.assertIn('收工', msg)
        time.sleep(0.5)
        self.assertFalse(alive(child), 'agent 應該被停掉')
        self.assertTrue(alive(parent.pid), '主程式要留著把做完的收下')
        self.assertTrue(jobrun.finishing(path))
        self.assertTrue(jobrun.read(path, (MARK,))['finishing'])

        ok, msg = jobrun.control(path, (MARK,), 'stop')
        self.assertTrue(ok)
        parent.wait(timeout=5)
        self.assertFalse(jobrun.finishing(path))
        self.assertEqual(jobrun.read(path, (MARK,))['phase'], 'stopped')

    def test_flows_without_graceful_stop_at_once(self):
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='jobrun-test-'))
        path = os.path.join(d, 'status.json')
        parent = subprocess.Popen([sys.executable, '-c', PARENT], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        parent.stdout.readline()
        jobrun.write(path, {'phase': 'run', 'pid': parent.pid})
        jobrun.control(path, (MARK,), 'stop')
        parent.wait(timeout=5)
        self.assertFalse(jobrun.finishing(path))


LOCKER = ("import fcntl,sys,time  # " + MARK + "\n"
          "f=open(sys.argv[1],'w')\n"
          "print('go',flush=True)\n"
          "while True:\n"
          "    fcntl.flock(f,fcntl.LOCK_EX); time.sleep(0.5); fcntl.flock(f,fcntl.LOCK_UN); time.sleep(0.005)\n")


class PauseKeepsTheBoardWritable(unittest.TestCase):
    def test_pause_never_freezes_it_while_it_holds_the_board_lock(self):
        # 它幾乎一直拿著看板的鎖(寫看板的半路上)。以前直接凍住:暫停多久,他在看板上的存檔就卡多久
        import fcntl
        from contextlib import contextmanager
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='jobrun-pause-'))
        path, lock = os.path.join(d, 'status.json'), os.path.join(d, 'board.html.lock')
        p = subprocess.Popen([sys.executable, '-c', LOCKER, lock], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and (os.kill(p.pid, 18), p.kill()))
        p.stdout.readline(); time.sleep(0.2)
        jobrun.write(path, {'phase': 'run', 'pid': p.pid})

        @contextmanager
        def hold():
            with open(lock, 'w') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        self.assertTrue(jobrun.control(path, (MARK,), 'pause', hold=hold())[0])
        with open(lock, 'w') as f:                  # 看板存檔:暫停中也要拿得到鎖
            end = time.time() + 2
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except OSError:
                    self.assertLess(time.time(), end, '暫停中看板的鎖拿不到,存檔會卡住')
                    time.sleep(0.05)
            fcntl.flock(f, fcntl.LOCK_UN)
        jobrun.control(path, (MARK,), 'stop')


if __name__ == '__main__':
    unittest.main()
