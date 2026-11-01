# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from activitytrack import i18n
from activitytrack_cli.main import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        # the texts the tests read: Italian, whatever the machine's language
        self.env = mock.patch.dict(os.environ, {"LC_ALL": "it_IT.UTF-8"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        i18n.set_language("it")
        self.dir.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data", self.dir.name, "--no-sync", *argv])
        return code, out.getvalue(), err.getvalue()

    def json(self, *argv):
        code, out, err = self.run_cli("--json", *argv)
        self.assertEqual(code, 0, err + out)
        return json.loads(out)

    def test_an_activity_from_start_to_complete(self):
        started = self.json("start", "Configurazione", "VPN", "-c", "Comune di Valdora", "-t", "urgente,rete")
        self.assertEqual((started["label"], started["mode"], started["tags"]),
                         ("Comune di Valdora · Configurazione VPN", "WORK", ["urgente", "rete"]))
        self.assertEqual(self.json("pause")["mode"], "PAUSE")
        self.assertEqual(self.json("resume")["mode"], "WORK")
        status = self.json("status")
        self.assertEqual([r["id"] for r in status["running"]], [started["id"]])
        self.assertEqual(status["sync"]["state"], "idle")
        # client and description: closed at once
        self.assertEqual(self.json("stop")["status"], "CLOSED")
        # without a description: left to complete, then completed
        other = self.json("start", "-c", "Comune di Valdora")
        self.assertEqual(self.json("stop", other["id"])["status"], "TO_COMPLETE")
        done = self.json("complete", other["id"], "-d", "Cablaggio")
        self.assertEqual((done["status"], done["activity"]), ("CLOSED", "Cablaggio"))
        home = self.json("list", "--period", "all", "--show", "done")
        self.assertEqual(sum(len(g["items"]) for g in home), 2)

    def test_the_activities_running_when_another_starts(self):
        a = self.json("start", "A", "-c", "Uno")
        b = self.json("start", "B", "-c", "Due")
        self.json("pause", b["id"])
        c = self.json("start", "C", "-c", "Tre")
        modes = {r["id"]: (r["status"], r["mode"]) for r in self.json("status")["running"]}
        # the one at work suspended, the paused one left as it is (as the desktop)
        self.assertEqual(modes[a["id"]], ("SUSPENDED", None))
        self.assertEqual(modes[b["id"]], ("ACTIVE", "PAUSE"))
        self.assertEqual(modes[c["id"]], ("ACTIVE", "WORK"))

    def test_to_dos(self):
        t = self.json("todo", "add", "Rinnovo", "certificati", "-c", "Hotel", "--plan", "domani 9:30 45",
                      "--priority", "2")
        self.assertEqual((t["title"], t["priority"], t["plan_start_min"], t["duration_min"]),
                         ("Rinnovo certificati", 2, 570, 45))
        self.assertEqual(self.json("todo", "postpone", t["id"], "30")["plan_start_min"], 600)
        started = self.json("todo", "start", t["id"][:4])
        self.assertEqual((started["label"], started["client"]), ("Hotel · Rinnovo certificati", "Hotel"))
        self.assertEqual(self.json("todo", "done", t["id"])["state"], "done")
        self.assertEqual(self.json("todo", "list"), [])

    def test_errors(self):
        code, out, err = self.run_cli("pause")
        self.assertEqual(code, 1)
        self.assertIn("Nessuna attività in corso", err)
        code, out, err = self.run_cli("--json", "start", "x")
        self.assertEqual((code, json.loads(out)["code"]), (1, "client_required"))
        code, out, err = self.run_cli("todo", "done", "zz")
        self.assertIn("almeno 3", err)

    def test_language(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "de_DE.UTF-8"}):
            code, out, err = self.run_cli("status")
        self.assertIn("Keine laufende Tätigkeit", out)
