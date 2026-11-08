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
                    mock.patch("activitytrack.updates.distro", return_value="deb13"):
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
