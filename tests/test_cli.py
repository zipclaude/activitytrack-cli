# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import hashlib
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

    def test_an_activity_from_start_to_stop(self):
        started = self.json("start", "Configurazione", "VPN", "-c", "Comune di Valdora", "-t", "urgente,rete")
        self.assertEqual((started["label"], started["mode"], started["tags"], started["display"]),
                         ("Comune di Valdora · Configurazione VPN", "WORK", ["urgente", "rete"], "WORK"))
        self.assertEqual(self.json("pause")["display"], "PAUSE")
        self.assertEqual(self.json("resume")["display"], "WORK")
        status = self.json("status")
        self.assertEqual([r["id"] for r in status["running"]], [started["id"]])
        self.assertEqual(status["sync"]["state"], "idle")
        self.assertEqual(self.json("stop")["display"], "DONE")
        # without a client the closed activity is INCOMPLETE, and a client completes it
        other = self.json("start", "-c", "Comune di Valdora")
        self.assertEqual(self.json("stop", other["id"])["display"], "DONE")
        self.json("edit", other["id"], "-c", "")
        self.assertEqual(self.json("list", "--period", "all", "--show", "missing")[0]["items"][0]["display"],
                         "INCOMPLETE")
        self.json("edit", other["id"], "-d", "Cablaggio", "-c", "Comune di Valdora")
        home = self.json("list", "--period", "all", "--show", "done")
        self.assertEqual(sum(len(g["items"]) for g in home), 2)
        reopened = self.json("reopen", "--yes", other["id"])
        self.assertEqual(reopened["display"], "WORK")
        self.json("stop", other["id"])
        self.assertEqual(self.json("delete", "--yes", other["id"])["deleted"], other["uid"])
        self.assertEqual(self.json("restore", other["id"])["display"], "DONE")

    def test_the_activities_running_when_another_starts(self):
        a = self.json("start", "A", "-c", "Uno")
        b = self.json("start", "B", "-c", "Due")
        self.json("pause", b["id"])
        c = self.json("start", "C", "-c", "Tre")
        shown = {r["id"]: r["display"] for r in self.json("status")["running"]}
        # the one at work and the paused one are both suspended (§3)
        self.assertEqual(shown, {a["id"]: "SUSPENDED", b["id"]: "SUSPENDED", c["id"]: "WORK"})
        self.assertEqual(self.json("resume", a["id"])["display"], "WORK")

    def test_a_command_tells_what_it_moved(self):
        from datetime import datetime, timedelta
        ahead = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        a = self.json("start", "Rete", "-c", "Uno")
        self.json("stop", a["id"], "--at", ahead)
        code, out, err = self.run_cli("start", "Cavi", "-c", "Due")
        self.assertEqual(code, 0)
        self.assertIn("La fine di «Rete» è stata anticipata all'inizio della nuova attività.", err)
        # once: the next command has nothing to say; in JSON it is the "notices" of the answer
        self.assertEqual(self.json("pause")["notices"], [])
        b = self.json("status")["running"][0]
        self.json("stop", b["id"], "--at", ahead)
        started = self.json("start", "Cavi", "-c", "Due")
        self.assertEqual(started["notices"], ["La fine di «Cavi» è stata anticipata all'inizio della nuova attività."])

    def test_the_notices_of_a_sync_are_said_once_and_dismissed(self):
        from activitytrack.app import App
        from activitytrack_cli.commands import _after_change
        app = App(self.dir.name)
        try:
            import time
            app.session.store.set_state("sync.notices", [{"code": "edit_dropped", "title": "Ticket",
                                                          "at": int(time.time() * 1000)}])
            args = mock.Mock(no_sync=False, json=False)
            out = mock.Mock()
            with mock.patch.object(App, "paired", True), mock.patch.object(App, "sync", return_value=("ok", "")):
                state, notices = _after_change(app, args, out)
            self.assertEqual(state, "ok")
            self.assertIn("«Ticket»", notices[0])
            out.error.assert_called_once_with(notices[0])
            self.assertEqual(app.session.store.notices(), [])
        finally:
            app.session.store.close()

    def test_there_is_no_suspend_command(self):
        with self.assertRaises(SystemExit):
            self.run_cli("suspend")

    def test_travel_from_work_asks_whether_the_work_is_complete(self):
        a = self.json("start", "A", "-c", "Uno")
        # without a flag a script gets "not completed": the same activity travels
        first = self.json("travel")
        self.assertEqual((first["uid"], first["display"]), (a["uid"], "TRAVEL"))
        self.json("resume", a["id"])
        other = self.json("travel", "--completed", a["id"])
        self.assertNotEqual(other["uid"], a["uid"])
        self.assertEqual((other["display"], other["client"]), ("TRAVEL", ""))
        self.assertEqual(self.json("list", "--period", "all", "--show", "done")[0]["items"][0]["uid"], a["uid"])

    def test_the_stop_choices_with_a_return_trip(self):
        self.json("start", "-c", "Uno", "--travel")
        a = self.json("resume")["uid"][:6]
        with mock.patch("activitytrack.work.Work.return_trip_offered", return_value=True):
            self.assertEqual(self.json("stop", "--return-trip", a)["display"], "TRAVEL")
        self.assertEqual(self.json("stop", "--end", a)["display"], "DONE")

    def test_an_absence(self):
        a = self.json("absence", "2026-11-12", "2026-11-13", "--kind", "holiday", "--from-time", "9:00",
                      "--to-time", "13:00", "--notes", "mare")
        self.assertEqual((a["display"], a["absence_kind"], a["absence_min"], a["label"]),
                         ("ABSENCE", "HOLIDAY", 480, "Ferie · mare"))

    def test_to_dos(self):
        t = self.json("todo", "add", "Rinnovo", "certificati", "-c", "Hotel", "--plan", "domani 9:30 45",
                      "--priority", "2")
        self.assertEqual((t["activity"], t["priority"], t["priority_label"], t["plan_start_min"], t["duration_min"]),
                         ("Rinnovo certificati", 2, "Media", 570, 45))
        self.assertEqual(self.json("todo", "postpone", t["id"], "30")["plan_start_min"], 600)
        edited = self.json("todo", "edit", t["id"], "--priority", "5")
        self.assertEqual((edited["priority_label"], edited["client"], edited["plan_start_min"]),
                         ("Emergenza", "Hotel", 600))
        started = self.json("todo", "start", t["id"][:4])
        self.assertEqual((started["label"], started["client"]), ("Hotel · Rinnovo certificati", "Hotel"))
        self.json("stop", t["id"])
        u = self.json("todo", "add", "Altro")
        self.assertEqual(self.json("todo", "done", u["id"])["display"], "DONE")
        self.assertEqual(self.json("todo", "reopen", u["id"])["display"], "TODO")
        self.assertEqual(self.json("todo", "ignore", u["id"])["display"], "DELETED")
        self.assertEqual(self.json("todo", "reopen", u["id"])["display"], "TODO")
        self.json("todo", "done", u["id"])
        self.assertEqual(self.json("todo", "list"), [])

    # ---- updates from the paired server ----

    def server_announces(self, version="9.9.9", beta=None, key="signer", component="cli-tar"):
        """The data folder paired, with a manifest signed by the pinned key in the store."""
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from activitytrack import manifest
        from activitytrack.pairing import Account
        from activitytrack.store import Store
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "server")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
                .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
                .sign(k, hashes.SHA256()))
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        chain = cert.public_bytes(serialization.Encoding.PEM)
        self.sha = hashlib.sha256(b"the package").hexdigest()

        def release(v, channel):
            return {"version": v, "channel": channel, "packages": [
                {"component": component, "url": "/api/v1/update/files/" + self.sha, "sha256": self.sha}]}
        releases = [release(version, "prod")] + ([release(beta, "beta")] if beta else [])
        fingerprint = manifest.fingerprint(cert.public_bytes(serialization.Encoding.DER))
        store = Store(Path(self.dir.name) / "activitytrack.sqlite3")
        store.set_state("account", Account("https://at.example.com", None, "d", "anna", "Anna", "user", "X",
                                           fingerprint if key == "signer" else "cd" * 32).to_json())
        store.set_state("update.manifest", manifest.sign({"issued": "2026-11-07T10:00:00Z", "releases": releases},
                                                         pem, chain))
        store.close()

    def update(self, *argv, body=b"the package", as_json=False):
        class Down:
            """The server's files, streamed."""
            status = 200

            @contextlib.contextmanager
            def stream(self, path, headers=None):
                self.rest = body
                yield self

            def read(self, n=-1):
                piece, self.rest = self.rest[:n], self.rest[n:]
                return piece
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("activitytrack.sync.Runner.refresh_update", return_value=""))
            stack.enter_context(mock.patch("activitytrack.session.Session.http", return_value=Down()))
            stack.enter_context(mock.patch("activitytrack.manifest.from_package_manager", return_value=False))
            # the package of a Linux installation, whatever the system the tests run on (on Windows it is cli-windows)
            stack.enter_context(mock.patch("sys.platform", "linux"))
            return self.run_cli(*(["--json"] if as_json else []), "update", *argv)

    def test_update_needs_a_pairing_and_the_servers_key(self):
        code, out, err = self.update()
        self.assertEqual(code, 1)
        self.assertIn("Non associato", err)
        self.server_announces(key="other")
        code, out, err = self.update()
        self.assertEqual(code, 1)
        self.assertIn("non è firmato", err)

    def test_a_server_that_refuses_is_an_error_not_an_up_to_date_answer(self):
        self.server_announces()
        for answer, text in (("revoked", "revocato"), ("incompatible:server_too_old", "va aggiornato il server"),
                             ("no route", "non raggiungibile")):
            with mock.patch("activitytrack.sync.Runner.refresh_update", return_value=answer):
                code, out, err = self.run_cli("update")
            self.assertEqual(code, 1, answer)
            self.assertIn(text, err)

    @mock.patch("activitytrack_cli.main.__version__", "0.9.9")  # a pre-release would follow the betas by itself
    def test_update_check_and_its_exit_status(self):
        self.server_announces(version="0.0.1")
        code, out, err = self.update()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ultima versione", out)
        self.server_announces(version="9.9.9", beta="10.0.0-beta1")
        code, out, err = self.update(as_json=True)
        data = json.loads(out)
        self.assertEqual(code, 10, err)
        self.assertEqual((data["state"], data["version"], data["channel"], data["downloadable"]),
                         ("available", "9.9.9", "prod", True))
        code, out, err = self.update("--channel", "beta", as_json=True)
        self.assertEqual((code, json.loads(out)["version"]), (10, "10.0.0-beta1"))
        # said by the status too, from the last sync
        self.assertIn("9.9.9", self.run_cli("status")[1])

    def test_update_check_and_download_exclude_each_other(self):
        self.server_announces()
        with self.assertRaises(SystemExit) as stopped:
            self.update("--check", "--download")
        self.assertEqual(stopped.exception.code, 2)
        folder = Path(self.dir.name) / "updates"
        self.assertFalse(folder.exists() and list(folder.iterdir()))  # nothing was downloaded

    def test_update_download_is_checked_against_the_sha256(self):
        self.server_announces()
        code, out, err = self.update("--download", as_json=True)
        data = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(data["downloaded"]).read_bytes(), b"the package")
        self.assertTrue(Path(data["downloaded"]).name.endswith("cli-tar-9.9.9.tar.gz"))
        code, out, err = self.update("--download", body=b"another file")
        self.assertEqual(code, 1)
        self.assertIn("Scaricamento non riuscito", err)

    def test_an_installation_of_the_package_manager_is_not_offered_the_download(self):
        self.server_announces(component="cli-deb13")
        with mock.patch("activitytrack.manifest.from_package_manager", return_value=True):
            # the same patches as update(), the package manager's installation on top
            with mock.patch("activitytrack.sync.Runner.refresh_update", return_value=""), \
                    mock.patch("activitytrack.updates.distro", return_value="deb13"), \
                    mock.patch("sys.platform", "linux"):
                code, out, err = self.run_cli("update", "--download")
        self.assertEqual(code, 1)
        self.assertIn("gestore di pacchetti", err)
        self.assertFalse((Path(self.dir.name) / "updates").exists())

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


class InteractiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch("activitytrack_cli.main.system_language", return_value="it")
        self.env.start()

    def tearDown(self):
        self.env.stop()
        i18n.set_language("it")
        self.dir.cleanup()

    def session(self, *answers):
        """The interactive mode with these answers; the number of prompts left unanswered, the entries, stderr."""
        answers = iter(answers)
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=lambda _prompt="": next(answers, "")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["--data", self.dir.name, "--no-sync", "interactive"])
        self.assertEqual(code, 0)
        self.assertEqual(list(answers), [])
        from activitytrack.app import App
        app = App(self.dir.name)
        try:
            return app.work.entries(), err.getvalue()
        finally:
            app.session.store.close()

    def test_an_empty_time_cancels_the_command(self):
        # new, past work, description, "another" client, its name, no tags, then the time left empty
        entries, err = self.session("1", "3", "Rete", "1", "Uno", "", "")
        self.assertEqual((entries, err), ([], ""))
        # the end left empty
        entries, err = self.session("1", "3", "Rete", "1", "Uno", "", "9:00", "")
        self.assertEqual((entries, err), ([], ""))

    def test_a_wrong_time_is_an_error_not_the_end_of_the_session(self):
        entries, err = self.session("1", "3", "Rete", "1", "Uno", "", "ciao", "10:00")
        self.assertEqual(entries, [])
        self.assertIn("Orario non valido", err)


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


class Utf8Output(unittest.TestCase):
    def test_a_code_page_output_is_switched_to_utf8(self):
        # a pipe on Windows: the ANSI code page, which cannot write the status marks
        from activitytrack_cli.main import _utf8_output
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
            _utf8_output()
            print("▶ ·", end="")
        out.flush()
        self.assertEqual(out.buffer.getvalue().decode("utf-8"), "▶ ·")
