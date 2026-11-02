# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from activitytrack import i18n
from activitytrack_cli.main import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        # the texts the tests read: Italian, whatever the machine's language (on Windows not from LC_ALL)
        self.env = mock.patch("activitytrack_cli.main.system_language", return_value="it")
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
        with mock.patch("activitytrack_cli.main.system_language", return_value="de"):
            code, out, err = self.run_cli("status")
        self.assertIn("Keine laufende Tätigkeit", out)

    def test_the_sync_in_the_json_of_a_change(self):
        # not paired: the change stays here, and the answer says so
        self.assertEqual(self.json("start", "A", "-c", "Uno")["sync"], "unpaired")
        self.assertNotIn("sync", self.json("list"))

    def test_input_that_is_refused_without_a_traceback(self):
        code, out, err = self.run_cli("todo", "add", "Rinnovo", "--plan", "domani 9:30 quaranta")
        self.assertEqual(code, 1)
        self.assertIn("Durata non valida: «quaranta»", err)
        code, out, err = self.run_cli("todo", "add", "Rinnovo", "--plan", "domani 9:30 ²")
        self.assertIn("Durata non valida", err)
        code, out, err = self.run_cli("list", "--show", "todo,pippo")
        self.assertEqual(code, 1)
        self.assertIn("«pippo» (validi: todo, missing, done, ignored)", err)

    def test_a_database_that_is_not_one(self):
        Path(self.dir.name, "activitytrack.sqlite3").write_bytes(b"garbage!" * 100)
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("I dati locali non sono leggibili", err)
        self.assertNotIn("Traceback", err)
        code, out, err = self.run_cli("--json", "status")
        self.assertEqual(json.loads(out)["code"], "database")

    def test_the_pairing_link_not_on_the_command_line(self):
        link = "activitytrack://pair?v=2&u=https%3A%2F%2Fa.b&c=x"
        with mock.patch("sys.stdin", io.StringIO(link + "\n")):
            code, out, err = self.run_cli("pair", "-")
        # read: refused for its version, so no server is needed
        self.assertIn("Collegamento di una versione", err)
        tty = mock.Mock(isatty=lambda: True)
        with mock.patch("sys.stdin", tty), mock.patch("getpass.getpass", return_value=link) as hidden:
            code, out, err = self.run_cli("pair")
        hidden.assert_called_once()
        self.assertIn("Collegamento di una versione", err)
        code, out, err = self.run_cli("pair", link)
        self.assertIn("Collegamento di una versione", err)


class TermTest(unittest.TestCase):
    def setUp(self):
        from activitytrack_cli.term import Out
        self.out = Out(io.StringIO(), colour=False)

    def test_choose_takes_ascii_digits_only(self):
        from activitytrack_cli.term import choose
        with mock.patch("builtins.input", side_effect=["²", "2"]), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(choose(self.out, "T", ["a", "b"]), 1)
        self.assertIn("Scegli un numero", err.getvalue())

    def test_ask_clears_with_a_dash(self):
        from activitytrack_cli.term import ask
        with mock.patch("builtins.input", side_effect=["-", "", "-"]) as prompt:
            self.assertEqual(ask(self.out, "Descrizione", "VPN"), "")
            self.assertIn("«-»", prompt.call_args[0][0])
            self.assertEqual(ask(self.out, "Descrizione", "VPN"), "VPN")
            # without a value to clear, a dash is a dash
            self.assertEqual(ask(self.out, "Descrizione"), "-")
