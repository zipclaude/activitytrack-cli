# SPDX-License-Identifier: GPL-3.0-or-later
"""
activitytrack: the commands. Every change goes to the shared data (the
desktop shows it at once) and is pushed at the end of the command, unless
--no-sync; without a network it leaves with the next sync. --json prints
what scripts read.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import activitytrack
from activitytrack.i18n import _, add_catalogs, set_language, system_language

from . import __version__

EXIT_ERROR = 1
# the states of --show
SHOW = ("todo", "missing", "done", "ignored")


def _language(folder):
    from activitytrack.policy import stored
    from activitytrack.store import Store
    forced = None
    db = Path(folder) / "activitytrack.sqlite3"
    if db.exists():
        try:
            store = Store(db)
            forced = stored(store).language
            store.close()
        except (sqlite3.Error, OSError):
            # the command says what is wrong with the data, in the system's language
            pass
    add_catalogs(Path(__file__).with_name("locale"))
    return set_language(forced or system_language())


def parser():
    p = argparse.ArgumentParser(prog="activitytrack", description=_(
        "ActivityTrack dalla riga di comando: le stesse attività e gli stessi Da fare del telefono, del web e "
        "dell'app desktop."))
    p.add_argument("--version", action="version", version=f"activitytrack {__version__}")
    p.add_argument("--data", help=_("cartella dei dati (predefinita: quella condivisa con l'app desktop)"))
    p.add_argument("--json", action="store_true", help=_("risultato in JSON, per gli script"))
    p.add_argument("--no-sync", action="store_true", help=_("non sincronizzare dopo una modifica"))
    p.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-i", "--interactive", action="store_true", help=_("modalità interattiva, a liste"))
    sub = p.add_subparsers(dest="command", metavar=_("comando"))

    sub.add_parser("status", help=_("attività in corso e stato della sincronizzazione"))

    s = sub.add_parser("start", help=_("avvia un'attività"))
    s.add_argument("description", nargs="*", help=_("descrizione"))
    s.add_argument("-c", "--client", help=_("cliente (dall'elenco o nuovo)"))
    s.add_argument("--company", help=_("azienda"))
    s.add_argument("--role", help=_("ruolo"))
    s.add_argument("-t", "--tag", action="append", default=[], help=_("tag (anche più volte, o separati da virgole)"))
    s.add_argument("--travel", action="store_true", help=_("un viaggio invece del lavoro"))
    s.add_argument("--todo", help=_("avvia dal Da fare indicato"))
    s.add_argument("--at", help=_("iniziata alle (HH:MM, o giorno e ora)"))
    s.add_argument("--until", help=_("già finita: fine (HH:MM, o giorno e ora)"))
    s.add_argument("--others", choices=["suspend", "stop", "keep"], default="suspend",
                   help=_("le attività già in corso: sospese (predefinito), fermate o lasciate in corso"))

    for name, text in (("pause", _("pausa")), ("resume", _("riprende il lavoro")), ("travel", _("viaggio")),
                       ("suspend", _("sospende")), ("stop", _("ferma"))):
        c = sub.add_parser(name, help=text)
        c.add_argument("id", nargs="?", help=_("attività (se ce n'è più di una in corso)"))
        c.add_argument("--at", help=_("all'ora (HH:MM)"))

    c = sub.add_parser("complete", help=_("completa un'attività con i dettagli mancanti"))
    c.add_argument("id")
    c.add_argument("-d", "--description")
    c.add_argument("-c", "--client")
    c.add_argument("--empty", action="store_true", help=_("chiudi anche senza descrizione"))
    c.add_argument("--todo-done", action="store_true", help=_("il Da fare da cui veniva è completato"))

    c = sub.add_parser("edit", help=_("modifica un'attività"))
    c.add_argument("id")
    c.add_argument("-d", "--description")
    c.add_argument("-c", "--client")
    c.add_argument("--company")
    c.add_argument("--role")
    c.add_argument("--locality")
    c.add_argument("--notes")
    c.add_argument("-t", "--tag", action="append", help=_("i tag (sostituiscono quelli presenti)"))

    c = sub.add_parser("reopen", help=_("riapre un'attività chiusa"))
    c.add_argument("id")
    c = sub.add_parser("delete", help=_("elimina un'attività"))
    c.add_argument("id")
    c.add_argument("--yes", action="store_true", help=_("senza chiedere conferma"))

    c = sub.add_parser("list", help=_("la lista della Home: Da fare e attività ferme"))
    _list_options(c)

    t = sub.add_parser("todo", help=_("i Da fare"))
    tsub = t.add_subparsers(dest="todo_command", metavar=_("azione"))
    _list_options(tsub.add_parser("list", help=_("i Da fare")))
    a = tsub.add_parser("add", help=_("nuovo Da fare"))
    a.add_argument("title", nargs="+")
    _todo_fields(a)
    e = tsub.add_parser("edit", help=_("modifica un Da fare"))
    e.add_argument("id")
    e.add_argument("--title")
    _todo_fields(e)
    for name, text in (("start", _("avvia un'attività dal Da fare")), ("done", _("fatto")),
                       ("ignore", _("ignora")), ("reopen", _("riapre")), ("delete", _("elimina"))):
        x = tsub.add_parser(name, help=text)
        x.add_argument("id")
    x = tsub.add_parser("postpone", help=_("posticipa il piano"))
    x.add_argument("id")
    x.add_argument("minutes", nargs="?", type=int, help=_("minuti (predefinito: quelli della policy)"))

    sub.add_parser("sync", help=_("sincronizza ora"))
    c = sub.add_parser("pair", help=_("associa il PC a un server ActivityTrack Web"))
    c.add_argument("link", nargs="?", help=_(
        "il collegamento della pagina «Dispositivi» del web; senza, viene chiesto senza mostrarlo "
        "(«-»: letto dallo standard input)"))
    c = sub.add_parser("update", help=_("controlla se il server ha una versione nuova (--download: la scarica)"))
    c.add_argument("--check", action="store_true", help=_(
        "solo controlla (predefinito); stato di uscita 10 se c'è una versione nuova"))
    c.add_argument("--download", action="store_true", help=_(
        "scarica il pacchetto e ne controlla l'impronta; non per chi ha installato dal gestore di pacchetti"))
    c.add_argument("--channel", action="append", metavar="CANALE", help=_(
        "canale in più di prod per questa volta (beta, test, dev…); si può ripetere"))
    sub.add_parser("unpair", help=_("dissocia il PC (i dati restano)"))
    sub.add_parser("interactive", help=_("modalità interattiva, a liste"))
    return p


def _list_options(c):
    c.add_argument("--period", choices=["today", "24h", "3d", "7d", "30d", "all"], default="today")
    c.add_argument("--show", default="todo,missing",
                   help=_("stati, separati da virgole: todo, missing, done, ignored"))
    c.add_argument("--company")
    c.add_argument("--tag")


def _todo_fields(c):
    c.add_argument("-c", "--client")
    c.add_argument("--company")
    c.add_argument("--notes")
    c.add_argument("--refs", help=_("riferimenti (ticket, link), separati da virgole"))
    c.add_argument("--due", help=_("scadenza: giorno [ora]"))
    c.add_argument("--plan", help=_("pianificazione: giorno [ora [durata in minuti]]"))
    c.add_argument("--priority", type=int, choices=range(1, 6))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    activitytrack.set_client("ActivityTrackCli", __version__)
    # the data folder first: its policy may fix the language, set before the texts are built
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--data")
    known, _rest = pre.parse_known_args(argv)
    from activitytrack.paths import data_dir
    folder = Path(known.data) if known.data else data_dir()
    _language(folder)

    args = parser().parse_args(argv)
    from . import commands
    from .term import Out
    out = Out()
    app = None
    try:
        from activitytrack.app import App
        app = App(folder, dev=args.dev)
        if args.interactive or args.command == "interactive":
            from .interactive import run
            return run(app, out, sync=not args.no_sync)
        return commands.dispatch(app, args, out)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        from activitytrack.app import InputError, describe_error
        from activitytrack.work import WorkError
        if isinstance(e, (InputError, WorkError)):
            text, code = describe_error(e), getattr(e, "code", "")
        elif isinstance(e, sqlite3.Error):
            # a database that is not one, or locked beyond the timeout
            text, code = _("I dati locali non sono leggibili (%s): %s") % (folder, e), "database"
        elif isinstance(e, OSError):
            text, code = _("Errore di sistema: %s") % e, "system"
        elif isinstance(e, ValueError):
            # an input the checks above let through: said, not a traceback
            text, code = _("Valore non valido: %s") % e, "value"
        else:
            raise
        if args.json:
            print(json.dumps({"error": text, "code": code}, ensure_ascii=False))
        else:
            out.error(text)
        return EXIT_ERROR
    finally:
        if app is not None:
            app.store.close()
