# SPDX-License-Identifier: GPL-3.0-or-later
"""
The interactive mode: numbered lists to choose from, in colour when the
terminal supports it. The same actions as the commands; every change is
pushed at once.
"""

from activitytrack import texts
from activitytrack.i18n import _
from activitytrack.work import STOP, TO_COMPLETE, TRAVEL, WORK, StartRequest, WorkError

from activitytrack.app import InputError as CliError, describe_error, one_line, parse_when
from .commands import show_entry
from .term import ask, choose, confirm


def run(app, out, sync=True):
    while True:
        out.line()
        running = app.work.running()
        if running:
            out.heading(_("In corso"))
            for e in running:
                show_entry(app, out, app.entry_info(e))
        else:
            out.line(_("Nessuna attività in corso"))
        info = app.sync_info()
        out.line(out.paint(info["text"], "red" if info["attention"] else "grey"))
        options = [one_line(app.work.label(e)) for e in running]
        actions = [("entry", e) for e in running]
        for label, action in ((_("Nuova attività"), "new"), (_("Da fare"), "todos"),
                              (_("Dettagli mancanti"), "missing"), (_("Sincronizza ora"), "sync")):
            options.append(out.paint(label, "bold"))
            actions.append((action, None))
        i = choose(out, "ActivityTrack", options, allow_back=False)
        if i is None:
            return 0
        kind, obj = actions[i]
        try:
            if kind == "entry":
                _entry(app, out, obj)
            elif kind == "new":
                _new(app, out)
            elif kind == "todos":
                _todos(app, out)
            elif kind == "missing":
                _missing(app, out)
            elif kind == "sync":
                _sync(app, out)
                continue
        except (CliError, WorkError) as e:
            out.error(describe_error(e))
            continue
        if sync and app.paired:
            _sync(app, out, quiet=True)


def _sync(app, out, quiet=False):
    state, detail = app.sync(full=True)
    if not quiet or state not in ("ok", "idle"):
        ok = state in ("ok", "idle")
        out.line(out.paint(texts.sync_state(app.session, state, detail), "green" if ok else "red"))


def _entry(app, out, e):
    commands = texts.entry_commands(e, app.work.policy)
    labels = [label for label, _cmd in commands] + [_("Modifica la descrizione"), _("Modifica il cliente")]
    i = choose(out, one_line(app.work.label(e)), labels)
    if i is None:
        return
    if i < len(commands):
        cmd = commands[i][1]
        status = app.work.command(e["uid"], cmd)
        if cmd == STOP and status == TO_COMPLETE:
            _complete(app, out, app.work.entry(e["uid"]))
    elif i == len(commands):
        text = ask(out, _("Descrizione"), e.get("activity") or "")
        if text is not None:
            app.work.edit(e["uid"], activity=text)
    else:
        client = _pick_client(app, out, e.get("clientUid"))
        if client is not None:
            app.work.edit(e["uid"], client=client)


def _complete(app, out, e):
    """The stop sheet: description, client, the to-do done."""
    out.line(out.paint(_("Completa l'attività"), "bold"))
    text = ask(out, _("Descrizione"), e.get("activity") or "")
    if text is None:
        return
    client = e.get("clientUid") or _pick_client(app, out, None)
    app.work.edit(e["uid"], activity=text, client=client or "")
    todo = app.store.get("todo", e["todoUid"]) if e.get("todoUid") else None
    if todo and not todo.get("done") and confirm(out, _("Il Da fare «%s» è completato") % todo.get("title"), True):
        app.todos.set_done(todo["uid"])
    allow_empty = False
    if not text:
        allow_empty = confirm(out, _("Chiudere senza descrizione? Va bene quando il lavoro si capisce dal cliente."))
        if not allow_empty:
            out.line(_("Resta tra i dettagli mancanti."))
            return
    app.work.complete(e["uid"], allow_empty=allow_empty)


def _pick_client(app, out, current):
    clients = app.work.masters("client")
    labels = [c.get("name") or "" for c in clients] + [out.paint(_("Un altro (scrivilo)…"), "bold")]
    i = choose(out, texts.FIELDS["client"], labels)
    if i is None:
        return None
    if i < len(clients):
        return clients[i]["uid"]
    return ask(out, texts.FIELDS["client"]) or None


def _new(app, out):
    out.line(out.paint(_("Nuova attività"), "bold"))
    text = ask(out, _("Descrizione"))
    if text is None:
        return
    client = _pick_client(app, out, None)
    tags = ask(out, _("Tag (separati da virgole)")) or ""
    mode = WORK
    if app.work.policy.feature("travel") and confirm(out, _("È un viaggio?")):
        mode = TRAVEL
    when = ask(out, _("Iniziata alle (vuoto: adesso)"))
    req = StartRequest(activity=text, client=client, mode=mode,
                       tags=[t.strip() for t in tags.split(",") if t.strip()],
                       start_ms=parse_when(when) if when else None)
    running = [e for e in app.work.running() if e.get("status") == "ACTIVE"]
    if running and app.work.policy.feature("concurrentEntries"):
        keep = confirm(out, _("Lasciare in corso le attività già avviate?"))
        if not keep:
            req.others = {e["uid"]: ("STOP" if e.get("mode") == "PAUSE" else "SUSPEND") for e in running}
    app.work.start(req)


def _todos(app, out):
    groups = app.todos.home(["TODO"], 0)
    items = [obj for _s, rows in groups for kind, obj, _x in rows if kind == "todo"]
    if not items:
        out.line(_("Nessun Da fare aperto."))
    labels = []
    for t in items:
        info = app.todo_info(t)
        colour = "red" if info["state"] == "overdue" else None
        title = out.paint(info["title"], *(["bold", colour] if colour else ["bold"]))
        labels.append(f"{title}  {out.paint(' · '.join(x for x in (info['client'], info['when']) if x), 'grey')}")
    labels.append(out.paint(_("Nuovo Da fare"), "bold"))
    i = choose(out, _("Da fare"), labels)
    if i is None:
        return
    if i == len(items):
        title = ask(out, _("Titolo"))
        if title:
            client = _pick_client(app, out, None)
            app.todos.save(None, title, client=client)
        return
    t = items[i]
    acts = [(_("Avvia"), "start"), (_("Fatto"), "done")]
    if t.get("planDate") and app.work.policy.feature("planning"):
        acts.append((_("Posticipa"), "postpone"))
    if app.work.policy.feature("ignoreTodos"):
        acts.append((_("Ignora"), "ignore"))
    j = choose(out, t.get("title") or "", [a for a, _x in acts])
    if j is None:
        return
    act = acts[j][1]
    if act == "start":
        running = [e for e in app.work.running() if e.get("status") == "ACTIVE"]
        others = {e["uid"]: ("STOP" if e.get("mode") == "PAUSE" else "SUSPEND") for e in running}
        app.work.start(StartRequest(activity=t.get("title") or "", client=t.get("clientUid"),
                                    company=t.get("companyUid"), todo=t["uid"], others=others))
    elif act == "done":
        app.todos.set_done(t["uid"])
    elif act == "postpone":
        app.todos.postpone(t["uid"])
    else:
        app.todos.set_ignored(t["uid"])


def _missing(app, out):
    entries = [e for e in app.work.entries() if e.get("status") == TO_COMPLETE]
    if not entries:
        out.line(_("Nessuna attività con dettagli mancanti."))
        return
    i = choose(out, _("Dettagli mancanti"), [one_line(app.work.label(e)) for e in entries])
    if i is not None:
        _complete(app, out, entries[i])
