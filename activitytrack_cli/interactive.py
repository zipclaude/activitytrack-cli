# SPDX-License-Identifier: GPL-3.0-or-later
"""
The interactive mode: numbered lists to choose from, in colour when the
terminal supports it. The same actions as the commands; every change is
pushed at once.
"""

import activitytrack
from activitytrack import texts
from activitytrack.i18n import _
from activitytrack.manifest import ManifestError
from activitytrack import homelist, model
from activitytrack.work import SUSPEND, TRAVEL, WORK, StartRequest, WorkError

from activitytrack.app import InputError as CliError, describe_error, one_line, parse_day, parse_minute, parse_when
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
        # the notices of the sync are in that line: said once
        app.session.store.dismiss_notices()
        options = [one_line(app.label(e)) for e in running]
        actions = [("entry", e) for e in running]
        for label, action in ((_("Nuova attività"), "new"), (_("Da fare"), "todos"),
                              (_("Dettagli mancanti"), "missing"), (_("Sincronizza ora"), "sync"),
                              (_("Aggiornamenti"), "update")):
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
            elif kind == "update":
                _update(app, out)
                continue
        except (CliError, WorkError) as e:
            out.error(describe_error(e))
            _told(app, out)
            continue
        _told(app, out)
        if sync and app.paired:
            _sync(app, out, quiet=True)


def _sync(app, out, quiet=False):
    state, detail = app.sync(full=True)
    ok = state in ("ok", "idle")
    if not quiet or not ok:
        out.line(out.paint(texts.sync_state(app.session, state, detail), "green" if ok else "red"))
    elif texts.sync_notices(app.session):
        out.line(out.paint(" ".join(texts.sync_notices(app.session)), "red"))
    app.session.store.dismiss_notices()


def _told(app, out):
    """What the last command left to tell the user (an end moved back, a planned segment removed)."""
    for text in app.work.take_notices():
        out.error(text)


def _entry(app, out, e):
    work = app.work
    actions = texts.entry_actions(work, e)
    labels = [a.label for a in actions] + [_("Modifica la descrizione"), _("Modifica il cliente")]
    i = choose(out, one_line(app.label(e)), labels)
    if i is None:
        return
    if i < len(actions):
        _act(app, out, e, actions[i])
    elif i == len(actions):
        text = ask(out, _("Descrizione"), e.get("activity") or "")
        if text is not None:
            work.edit(e["uid"], activity=text)
    else:
        client = _pick_client(app, out, e.get("clientUid"))
        if client is not None:
            work.edit(e["uid"], client=client)


def _act(app, out, e, action):
    """One button of an activity, with the questions of §3: travel from work, the stop's three choices, reopen."""
    work, uid, verb = app.work, e["uid"], action.verb
    if verb == "travel_from_work":
        work.travel_from_work(uid, confirm(out, _("Hai completato l'attività lavorativa?"), False))
    elif verb == "stop":
        if work.return_trip_offered(uid):
            options = [_("Avvia viaggio di ritorno"), _("Termina l'attività")]
            if work.return_trip_addable(uid):
                options.append(_("Termina l'attività ed aggiungi il viaggio di ritorno"))
            i = choose(out, _("Fermi l'attività"), options)
            if i is None:
                return
            if i == 0:
                work.travel_back(uid)
            else:
                work.stop(uid, return_trip=i == 2)
        else:
            work.stop(uid)
    elif verb == "reopen":
        if confirm(out, _("Riaprire «%s»?") % one_line(app.label(e))):
            work.reopen(uid, action.kind)
    elif verb == "delete":
        if confirm(out, _("Eliminare «%s»? Si elimina anche sugli altri dispositivi.") % one_line(app.label(e))):
            work.delete(uid)
    else:
        work.perform(uid, verb, action.kind)


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
    """The four modes (§3): Da fare, Lavoro, Viaggio, Assenza; Lavoro also in the past."""
    work = app.work
    modes = [(_("Da fare"), "todo"), (_("Lavoro"), "work"), (_("Lavoro passato"), "past")]
    if work.policy.feature("travel"):
        modes.append((_("Viaggio"), "travel"))
    modes.append((_("Assenza"), "absence"))
    i = choose(out, _("Nuova attività"), [m for m, _k in modes])
    if i is None:
        return
    mode = modes[i][1]
    if mode == "todo":
        return _new_todo(app, out)
    if mode == "absence":
        return _new_absence(app, out)
    text, client, tags = "", None, ""
    if mode != "travel":
        text = ask(out, _("Descrizione"))
        if text is None:
            return
        client = _pick_client(app, out, None)
        tags = ask(out, _("Tag (separati da virgole)")) or ""
    start = end = None
    if mode == "past":
        start = ask(out, _("Iniziata alle (HH:MM, o giorno e ora)"))
        if not start:
            return
        end = ask(out, _("Finita alle (HH:MM, o giorno e ora)"))
        if not end:
            return
        start, end = parse_when(start), parse_when(end)
    elif mode == "work":
        when = ask(out, _("Iniziata alle (vuoto: adesso)"))
        start = parse_when(when) if when else None
    req = StartRequest(activity=text, client=client, mode=TRAVEL if mode == "travel" else WORK,
                       tags=[t.strip() for t in tags.split(",") if t.strip()], start_ms=start, end_ms=end)
    running = [e for e in work.running() if work.open_segment(e["uid"]) is not None]
    if end is None and running and work.policy.feature("concurrentEntries") and \
            not confirm(out, _("Lasciare in corso le attività già avviate?")):
        req.others = {e["uid"]: SUSPEND for e in running}
    work.start(req)


def _new_todo(app, out):
    title = ask(out, _("Titolo"))
    if not title:
        return
    client = _pick_client(app, out, None)
    labels = [texts.PRIORITIES[p] for p in model.PRIORITIES]
    p = choose(out, _("Priorità"), labels)
    app.work.save_todo(None, title, client=client, priority=p or 0)


def _new_absence(app, out):
    kinds = list(texts.ABSENCE_KINDS)
    i = choose(out, _("Tipo di assenza"), [texts.ABSENCE_KINDS[k] for k in kinds])
    if i is None:
        return
    first = parse_day(ask(out, _("Dal giorno")) or "")
    last = ask(out, _("Al giorno (vuoto: lo stesso)"))
    times = ask(out, _("Dalle (HH:MM, vuoto: l'orario di lavoro)"))
    end = ask(out, _("Alle (HH:MM)")) if times else None
    company = None
    if len(app.work.masters("company")) > 1:
        c = choose(out, texts.FIELDS["company"], [r.get("name") or "" for r in app.work.masters("company")])
        company = app.work.masters("company")[c]["uid"] if c is not None else None
    app.work.save_absence(None, first, parse_day(last) if last else None, company=company, absence_kind=kinds[i],
                          start_min=parse_minute(times) if times else None,
                          end_min=parse_minute(end) if end else None)


def _todos(app, out):
    groups = homelist.home(app.work, ["TODO"], 0)
    items = [obj for _s, rows in groups for kind, obj, _x in rows if kind == "todo"]
    if not items:
        out.line(_("Nessun Da fare aperto."))
    infos = [app.entry_info(t) for t in items]
    labels = []
    for info in infos:
        title = out.paint(info["activity"], "bold")
        extra = " · ".join(x for x in (info["client"], info["when"], info["priority_label"] if info["priority"] else "")
                           if x)
        labels.append(f"{out.state(info['display'], info['state'])}  {title}  {out.paint(extra, 'grey')}")
    labels.append(out.paint(_("Nuovo Da fare"), "bold"))
    i = choose(out, _("Da fare"), labels)
    if i is None:
        return
    if i == len(items):
        return _new_todo(app, out)
    t = items[i]
    acts = texts.entry_actions(app.work, t)
    if t.get("planDate") and app.work.policy.feature("planning"):
        acts.append(texts.Action(_("Posticipa"), "postpone"))
    j = choose(out, t.get("activity") or "", [a.label for a in acts])
    if j is None:
        return
    a = acts[j]
    if a.verb == "start":
        running = [e for e in app.work.running() if app.work.open_segment(e["uid"]) is not None]
        others = {e["uid"]: SUSPEND for e in running}
        app.work.start(StartRequest(todo=t["uid"], mode=a.kind, others=others))
    elif a.verb == "postpone":
        app.work.postpone(t["uid"])
    else:
        _act(app, out, t, a)


def _update(app, out):
    status = app.updates.check()
    out.line(out.paint(texts.update_state(status, activitytrack.client()["version"]),
                       "yellow" if status.release else "grey"))
    if status.downloadable and confirm(out, _("Scaricare il pacchetto?"), default=True):
        try:
            out.line(_("Scaricato e controllato: %s") % app.updates.download(status.release))
        except ManifestError as e:
            out.error(_("Scaricamento non riuscito: %s") % e)


def _missing(app, out):
    entries = [e for e in app.work.entries() if app.work.display(e)[0] == model.INCOMPLETE]
    if not entries:
        out.line(_("Nessuna attività con dettagli mancanti."))
        return
    i = choose(out, _("Dettagli mancanti"), [one_line(app.label(e)) for e in entries])
    if i is not None:
        _entry(app, out, entries[i])
